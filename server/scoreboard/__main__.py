"""指令列工具。

  python -m scoreboard serve            啟動伺服器（給 ESP32 連）
  python -m scoreboard preview --demo   用假資料輸出預覽 PNG（不需網路）
  python -m scoreboard preview          抓今天的真實資料輸出預覽 PNG
  python -m scoreboard probe cpbl       把某聯盟的原始回應存成 JSON，介面改版時用來除錯
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime
from pathlib import Path

from .aggregator import Aggregator, make_client
from .config import Config
from . import playlist
from .render import Renderer


def cmd_serve(a):
    import os

    import uvicorn

    os.environ["SCOREBOARD_CONFIG"] = a.config
    uvicorn.run("scoreboard.server:app", host=a.host, port=a.port)


async def _real_games(cfg: Config, day: date | None):
    agg = Aggregator(cfg)
    async with make_client() as client:
        await agg.refresh_all(client, day)
    for s in agg.states:
        print(f"  {s.league.name:5} {len(s.games)} 場 {('錯誤：' + s.error) if s.error else ''}")
    return agg.games()


def cmd_preview(a):
    cfg = Config.load(a.config)
    if a.demo:
        from .demo import demo_games

        games = demo_games(cfg)
    else:
        games = asyncio.run(_real_games(cfg, date.fromisoformat(a.date) if a.date else None))
    r = Renderer(cfg.display["width"], cfg.display["height"], cfg.display.get("fonts"))
    pages = playlist.build(r, cfg, games, datetime.now(cfg.tz))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(pages):
        Renderer.led_preview(p.image).save(out / f"page{i}_{p.kind}.png")
        p.image.save(out / f"page{i}_{p.kind}_raw.png")
    print(f"已輸出 {len(pages)} 頁到 {out.resolve()}")


async def _probe(cfg: Config, which: list[str], day: date, out: Path):
    from .leagues import ALL

    out.mkdir(parents=True, exist_ok=True)
    raw_dir = out / "raw"
    raw_dir.mkdir(exist_ok=True)
    counter = {"n": 0}

    async def save_response(r):
        # 把每一個 HTTP 回應原封不動存下來，官網改版時拿來對照欄位
        await r.aread()
        counter["n"] += 1
        name = f"{counter['n']:02d}_{r.request.method}_{r.url.host}{r.url.path}".replace("/", "_")[:120]
        body = f"{r.request.method} {r.url}\nHTTP {r.status_code}\n\n" + r.text
        (raw_dir / f"{name}.txt").write_text(body, encoding="utf-8")

    async with make_client(event_hooks={"response": [save_response]}) as client:
        for cls in ALL:
            if which and cls.key not in which:
                continue
            lg = cls()
            try:
                games = await lg.fetch(client, cfg, day)
                (out / f"{cls.key}_games.json").write_text(
                    json.dumps([g.to_dict() for g in games], ensure_ascii=False, indent=1), encoding="utf-8"
                )
                print(f"{cls.name:5} OK，{len(games)} 場")
                for g in games:
                    if g.race is not None:
                        top = " ".join(f"{d.pos}.{d.code}" for d in g.race.drivers[:5]) or "（還沒有名次）"
                        print(f"      {g.start:%H:%M} F1 {g.race.gp} {g.race.session}  [{g.status_text()}]  {top}")
                        continue
                    rhe = ""
                    if g.away.hits is not None:
                        rhe = f"  H {g.away.hits}-{g.home.hits} E {g.away.errors}-{g.home.errors}"
                    print(f"      {g.start:%H:%M} {g.away.name} {g.away.score} - {g.home.score} {g.home.name}"
                          f"  [{g.status_text()}]{rhe} 原始狀態={g.detail} {g.label} 來源={g.extra.get('source', '')}")
            except Exception as e:
                print(f"{cls.name:5} 失敗：{type(e).__name__}: {e}")


# 診斷用：找出中職官網（www.cpbl.com.tw）現在網頁背後用的資料網址
CPBL_PAGES = ["/", "/schedule", "/schedule/index", "/box", "/box/index", "/standings/season", "/stats/toplist"]
CPBL_PATH_RE = r"""['"](/(?:[A-Za-z]+/)*[A-Za-z]*(?:get|Get|list|List|live|Live|data|Data|score|Score)[A-Za-z]*)['"]"""


def _show(r, label: str = "") -> None:
    ct = r.headers.get("content-type", "")
    head = r.text[:300].replace("\n", " ").replace("\r", " ")
    print(f"  {r.status_code}  {r.request.method} {r.url.path}{label}  ({len(r.content)} bytes, {ct})")
    if r.status_code != 200:
        keep = {k: v for k, v in r.headers.items() if k.lower() in ("server", "x-powered-by", "x-azure-ref", "cf-ray", "location")}
        print("      標頭：", keep)
    print("      開頭：", head)


async def _diagnose_cpbl(client, raw_dir: Path) -> None:
    import re

    from .leagues.cpbl import BASE, find_token

    print("中職官網：")
    found: set[str] = set()
    token = None
    for i, path in enumerate(CPBL_PAGES, 1):
        try:
            r = await client.get(BASE + path, headers={"Referer": BASE + "/"})
        except Exception as e:
            print(f"  失敗  GET {path}  {type(e).__name__}: {e}")
            continue
        _show(r)
        (raw_dir / f"cpbl{i:02d}.txt").write_text(f"GET {r.url}\nHTTP {r.status_code}\n\n{r.text}", encoding="utf-8")
        if r.status_code == 200:
            token = token or find_token(r.text)
            # 網頁 JavaScript 裡呼叫的資料網址，例如 /home/getdetaillist
            found |= set(re.findall(CPBL_PATH_RE, r.text))
            links = sorted(set(re.findall(r'href="(/[^"#?]*)"', r.text)))
            print("      頁面連結：", links[:30])
    print("  token：", "有" if token else "沒有")
    home = (raw_dir / "cpbl01.txt").read_text(encoding="utf-8") if (raw_dir / "cpbl01.txt").exists() else ""
    # 首頁的比分區塊是直接寫在 HTML 裡，還是靠 JavaScript 另外抓
    for kw in ("getdetaillist", "GameSno", "中信兄弟", "味全龍", "台灣大賽"):
        for m in list(re.finditer(kw, home))[:2]:
            print(f"      首頁「{kw}」附近：", home[max(0, m.start() - 300): m.end() + 300].replace("\n", " ")[:700])
    print("  網頁裡找到的資料網址：", sorted(found))

    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    form = {"calendar": f"{today.year}/{today.month:02d}/01", "location": "", "kindCode": "C",
            "year": str(today.year), "date": today.strftime("%Y/%m/%d")}
    headers = {"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/", "Origin": BASE}
    if token:
        headers["RequestVerificationToken"] = token
    for path in sorted(found | {"/schedule/getgamedatas", "/home/getdetaillist", "/home/getgamedatas"})[:25]:
        try:
            r = await client.post(BASE + path, data=form, headers=headers)
            _show(r, " [POST]")
        except Exception as e:
            print(f"  失敗  POST {path}  {type(e).__name__}: {e}")

    # 實際跑一次程式裡的官網備援流程
    from .leagues.cpbl import CPBL

    try:
        games = await CPBL().fetch_official(client, Config.load(None), today)
        print(f"  官網備援流程 OK，今天 {len(games)} 場：", [f"{g.away.name}-{g.home.name} {g.status_text()}" for g in games])
    except Exception as e:
        print(f"  官網備援流程失敗：{type(e).__name__}: {e}")


# 診斷用：野球革命（Rebas）有哪些賽季代碼
DIAG_URLS = [
    "https://www.rebas.tw/api/leagues/CPBL",
]


def _summarize(url: str, r) -> None:
    import re

    if "rebas.tw" in url and r.status_code == 200:
        seasons = sorted(set(re.findall(r"CPBL-\d{4}-[A-Za-z0-9]+", r.text)))
        print("    出現的賽季代碼：", seasons[:40])


async def _diagnose(out: Path, cfg: Config | None = None):
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    async with make_client() as client:
        await _diagnose_cpbl(client, raw_dir)
        await _diagnose_f1(client, raw_dir)
        await _diagnose_f1_live(client, str(((cfg.league("f1") if cfg else {}) or {}).get("live_token") or ""))
        print("野球革命：")
        for i, url in enumerate(DIAG_URLS, 1):
            try:
                r = await client.get(url)
                print(f"  {r.status_code}  {url}  ({len(r.content)} bytes)")
                (raw_dir / f"diag{i:02d}.txt").write_text(f"GET {url}\nHTTP {r.status_code}\n\n{r.text}", encoding="utf-8")
                _summarize(url, r)
            except Exception as e:
                print(f"  失敗  {url}  {type(e).__name__}: {e}")


F1_URLS = [
    "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard",
    "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard?dates=20261025",
    "https://api.jolpi.ca/ergast/f1/current.json",
    "https://api.jolpi.ca/ergast/f1/current/last/results.json",
    "https://api.jolpi.ca/ergast/f1/current/last/qualifying.json",
]


async def _diagnose_f1(client, raw_dir: Path) -> None:
    """F1 資料來源的欄位結構（ESPN 沒有文件，要看實際回應）。"""

    def shape(v, depth=0, maxd=4):
        if depth > maxd:
            return "…"
        if isinstance(v, dict):
            return {k: shape(x, depth + 1, maxd) for k, x in list(v.items())[:40]}
        if isinstance(v, list):
            return [shape(v[0], depth + 1, maxd), f"×{len(v)}"] if v else []
        return v if not isinstance(v, str) or len(v) < 60 else v[:60] + "…"

    print("F1：")
    for i, url in enumerate(F1_URLS, 1):
        try:
            r = await client.get(url)
        except Exception as e:
            print(f"  失敗  {url}  {type(e).__name__}: {e}")
            continue
        print(f"  {r.status_code}  {url}  ({len(r.content)} bytes)")
        (raw_dir / f"f1_{i:02d}.txt").write_text(f"GET {url}\nHTTP {r.status_code}\n\n{r.text}", encoding="utf-8")
        if r.status_code != 200:
            continue
        data = r.json()
        if "espn" in url:
            for ev in data.get("events", [])[:3]:
                print("    event:", ev.get("name"), ev.get("date"), ev.get("status", {}).get("type", {}).get("name"))
                for c in ev.get("competitions", []):
                    print("      comp:", c.get("type"), c.get("date"), (c.get("status") or {}).get("type", {}).get("name"),
                          "period", (c.get("status") or {}).get("period"), "competitors", len(c.get("competitors", [])))
                comps = ev.get("competitions") or []
                done = [c for c in comps if (c.get("status") or {}).get("type", {}).get("completed")]
                if done:
                    q = done[-1]
                    print("      已結束賽段", q.get("type"), "status：", json.dumps(q.get("status"), ensure_ascii=False)[:600])
                    for x in sorted(q.get("competitors", []), key=lambda x: x.get("order", 99))[:3]:
                        print("        車手：", json.dumps(x, ensure_ascii=False)[:1200])
                if comps:
                    c = comps[-1]
                    print("      competition 欄位：", json.dumps(shape({k: v for k, v in c.items() if k != "competitors"}), ensure_ascii=False)[:1500])
                    if c.get("competitors"):
                        print("      competitor 範例：", json.dumps(c["competitors"][0], ensure_ascii=False)[:1500])
            print("    頂層欄位：", list(data.keys()))
        else:
            print("    結構：", json.dumps(shape(data, maxd=6), ensure_ascii=False)[:1800])


async def _diagnose_f1_live(client, token: str = "") -> None:
    """測試 F1 官方即時計時資料流能不能連上（有填 live_token 就用自己的帳號），並印出欄位。"""
    from datetime import datetime

    from .leagues.f1_live import LiveTiming, token_expiry

    lt = LiveTiming(token)
    print("F1 官方即時計時（BallLED 自己的 User-Agent，不偽裝）：")
    if token:
        exp = token_expiry(token)
        print("  使用 config.yaml 的 live_token，到期時間：", datetime.fromtimestamp(exp) if exp else "看不出來")
    else:
        print("  沒有填 live_token（官方從 2026 年 10 月起要求登入，沒有 token 通常會 403）")
    try:
        await asyncio.wait_for(lt.session(client, stop_after_snapshot=True), 30)
    except Exception as e:
        print(f"  失敗：{type(e).__name__}: {e}")
        return
    print("  連線成功，快照大小：", {k: len(json.dumps(v)) for k, v in lt.state.items()})
    print("  SessionInfo：", json.dumps(lt.state["SessionInfo"], ensure_ascii=False)[:700])
    print("  LapCount：", lt.state["LapCount"])
    print("  前五名：", lt.board()["drivers"][:5])
    dl = lt.state["DriverList"] or {}
    print("  DriverList 範例：", json.dumps(next(iter(dl.values()), {}), ensure_ascii=False)[:500])
    tl = (lt.state["TimingData"] or {}).get("Lines") or {}
    print("  TimingData 範例：", json.dumps(next(iter(tl.values()), {}), ensure_ascii=False)[:900])


def cmd_probe(a):
    cfg = Config.load(a.config)
    day = date.fromisoformat(a.date) if a.date else datetime.now(cfg.tz).date()
    asyncio.run(_probe(cfg, a.leagues, day, Path(a.out)))
    if a.diagnose:
        print("診斷：")
        asyncio.run(_diagnose(Path(a.out), cfg))


def main():
    ap = argparse.ArgumentParser(prog="scoreboard")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(required=True)

    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_serve)

    p = sub.add_parser("preview")
    p.add_argument("--demo", action="store_true", help="使用假資料預覽版面")
    p.add_argument("--date", help="YYYY-MM-DD，預設今天")
    p.add_argument("--out", default="preview")
    p.set_defaults(fn=cmd_preview)

    pr = sub.add_parser("probe")
    pr.add_argument("leagues", nargs="*", help="cpbl mlb nba tpbl plg，不填代表全部")
    pr.add_argument("--date", help="YYYY-MM-DD，預設今天")
    pr.add_argument("--out", default="probe")
    pr.add_argument("--diagnose", action="store_true", help="另外測試幾個備用網址")
    pr.set_defaults(fn=cmd_probe)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
