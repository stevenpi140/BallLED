"""HTTP 伺服器：ESP32 用 GET /frame?page=N 取得一頁 RGB565 畫面。

回應標頭：
  X-Page / X-Pages  這是第幾頁 / 總共幾頁
  X-Hold-Ms         這頁要停留多久（毫秒）
  X-Brightness      建議亮度 0-255
  X-Refresh-Ms      這頁有進行中的比賽時，停留期間每隔多久重抓一次（0 = 不用重抓）
  X-Chime-*         整點報時：距離下一次報時的毫秒數、報時編號、音檔版本、音量（見 chime.py）
  X-Marquee-*       整點跑馬燈：距離下一次跑馬燈的毫秒數、編號（見 marquee.py）
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO

from fastapi import FastAPI, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse

from . import playlist
from .aggregator import Aggregator, make_client
from .chime import Chime
from .config import Config
from .marquee import Marquee
from .render import Renderer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

cfg = Config.load(os.environ.get("SCOREBOARD_CONFIG", "config.yaml"))
agg = Aggregator(cfg)
chime = Chime(cfg)
renderer = Renderer(cfg.display["width"], cfg.display["height"], cfg.display.get("fonts"))
marquee = Marquee(cfg, chime, renderer)


@asynccontextmanager
async def lifespan(_app):
    agg.start()
    yield


app = FastAPI(title="Scoreboard", lifespan=lifespan)


_cache: dict = {"key": None, "pages": None}


def _pages():
    now = datetime.now(cfg.tz)
    games = agg.games()
    # 看板每 2 秒就會來抓一次：比賽資料沒變、又在同一分鐘內（開打倒數以分鐘為單位），就沿用上次畫好的頁面
    tomorrow = agg.showing_tomorrow()  # 今天的比賽都結束了：輪播明天的預告
    key = (now.strftime("%Y-%m-%d %H:%M"), tomorrow, repr(games))
    if _cache["key"] != key:
        note = ""
        if not games and agg.states and all(s.error and not s.last_ok for s in agg.states):
            note = "資料來源連線失敗"
        _cache["key"], _cache["pages"] = key, playlist.build(renderer, cfg, games, now, note, tomorrow)
    return now, games, _cache["pages"]


@app.get("/frame")
def frame(page: int = Query(0, ge=0)):
    now, games, pages = _pages()
    idx = page % len(pages)
    p = pages[idx]
    return Response(
        content=Renderer.rgb565(p.image),
        media_type="application/octet-stream",
        headers={
            "X-Page": str(idx),
            "X-Pages": str(len(pages)),
            "X-Hold-Ms": str(p.hold_ms),
            "X-Brightness": str(playlist.brightness(cfg, now, any(g.state == "live" for g in games))),
            "X-Refresh-Ms": str(int(cfg.raw["poll"]["screen_refresh_seconds"] * 1000) if p.live else 0),
            "Cache-Control": "no-store",
            **chime.headers(now),
            **marquee.headers(now),
        },
    )


@app.get("/marquee.bin")
def marquee_bin(id: str = Query("")):
    """整點跑馬燈的長圖（RGB565，高度等於畫面高度，寬度 = 檔案大小 / 高度 / 2）。"""
    img, params = marquee.strip(id)
    return Response(
        content=Renderer.rgb565(img),
        media_type="application/octet-stream",
        headers={"X-Marquee-Width": str(img.width), "Cache-Control": "no-store", **params},
    )


@app.post("/api/marquee/test")
def marquee_test():
    """讓看板下一次來抓畫面時馬上跑一次跑馬燈（不會播聲音）。"""
    if not marquee.enabled:
        return JSONResponse({"ok": False, "error": "config.yaml 的 marquee.enabled 還沒打開"}, status_code=400)
    now = datetime.now(cfg.tz)
    marquee.test(now)
    return {"ok": True, "text": marquee.text_for(now.strftime("%Y%m%d%H%M%S"))}


@app.get("/preview/marquee.png")
def preview_marquee(led: bool = True):
    img, _ = marquee.strip(datetime.now(cfg.tz).strftime("%Y%m%d%H0000"))
    if led:
        img = Renderer.led_preview(img)
    buf = BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/chime.pcm")
def chime_pcm():
    pcm = chime.pcm()
    if not pcm:
        return Response(chime.error or "整點報時沒有設定音檔", status_code=404, media_type="text/plain; charset=utf-8")
    return Response(pcm, media_type="application/octet-stream", headers={"X-Chime-Ver": chime.version()})


@app.post("/api/chime/test")
def chime_test():
    """讓看板馬上播一次（下一次來抓畫面時就會播），用來測試音量。"""
    if not chime.enabled:
        return JSONResponse({"ok": False, "error": "config.yaml 的 chime.enabled 還沒打開"}, status_code=400)
    if not chime.pcm():
        return JSONResponse({"ok": False, "error": chime.error}, status_code=400)
    chime.test(datetime.now(cfg.tz))
    return {"ok": True, "seconds": round(len(chime.pcm() or b"") / 2 / 48000, 1)}


@app.get("/preview/{page}.png")
def preview_png(page: int, led: bool = True):
    _, _, pages = _pages()
    img = pages[page % len(pages)].image
    if led:
        img = Renderer.led_preview(img)
    buf = BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/", response_class=HTMLResponse)
def index():
    _, _, pages = _pages()
    imgs = "".join(
        f'<figure><img src="/preview/{i}.png?t=0" data-i="{i}"><figcaption>{i}: {p.kind} · {p.hold_ms // 1000}s</figcaption></figure>'
        for i, p in enumerate(pages)
    )
    return f"""<!doctype html><meta charset="utf-8"><title>Scoreboard 預覽</title>
<style>body{{background:#111;color:#ccc;font-family:sans-serif;margin:16px}}
img{{width:min(768px,100%);image-rendering:pixelated;border-radius:8px}}figure{{margin:0 0 16px}}</style>
<h3>點陣看板預覽（每 10 秒更新）</h3>{imgs}
<p><a href="/api/games" style="color:#8cf">/api/games</a> · <a href="/api/status" style="color:#8cf">/api/status</a></p>
<script>setInterval(()=>document.querySelectorAll('img').forEach(i=>i.src=`/preview/${{i.dataset.i}}.png?t=${{Date.now()}}`),10000)</script>"""


@app.get("/api/games")
def api_games():
    return JSONResponse([g.to_dict() for g in agg.games()])


@app.get("/api/status")
def api_status():
    _, games, pages = _pages()
    return {
        "updated_at": agg.updated_at,
        "games": len(games),
        "showing": "tomorrow" if agg.showing_tomorrow() else "today",  # tomorrow = 今天比賽都結束，輪播明日預告
        "pages": len(pages),
        "cycle_seconds": sum(p.hold_ms for p in pages) // 1000,  # 輪播一圈多久，每一場都會在這段時間內出現一次
        "leagues": agg.status(),
        "chime": _chime_status(),
        "marquee": _marquee_status(),
    }


def _chime_status() -> dict:
    if not chime.enabled:
        return {"enabled": False}
    nxt = chime.next_chime(datetime.now(cfg.tz))
    return {"enabled": True, "next": nxt.isoformat() if nxt else None, "error": chime.error,
            "seconds": round(len(chime.pcm() or b"") / 2 / 48000, 1)}


def _marquee_status() -> dict:
    if not marquee.enabled:
        return {"enabled": False}
    nxt = marquee.next_at(datetime.now(cfg.tz))
    return {"enabled": True, "next": nxt.isoformat() if nxt else None,
            "text": marquee.text_for(nxt.strftime("%Y%m%d%H%M%S")) if nxt else None}


@app.post("/api/refresh")
async def api_refresh():
    async with make_client() as client:
        await agg.refresh_all(client)
    return api_status()
