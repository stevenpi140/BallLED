"""決定要輪播哪些頁面、每頁停多久，以及目前的亮度。"""

from __future__ import annotations

from datetime import datetime, time as dtime

from .config import Config
from .models import Game
from .render import Page, Renderer


def build(r: Renderer, cfg: Config, games: list[Game], now: datetime, note: str = "",
          tomorrow: bool = False) -> list[Page]:
    """tomorrow=True：今天的比賽都結束了，games 是明天的賽程（標題顯示「明日」）。"""
    disp = cfg.display
    hold = int(disp["page_seconds"] * 1000)
    live_hold = int(disp["live_page_seconds"] * 1000)
    if not games:
        return [Page("idle", 15000, r.idle(now, note))]
    if len(games) == 1:
        g = games[0]
        return [_game_page(r, g, now, hold, live_hold, tomorrow)]
    # 總覽頁和單場頁穿插：總覽(1-4) → 第1-4場 → 總覽(5-8) → 第5-8場 ...
    # 每一圈每一場都會輪到一次，比賽再多也不會漏掉
    pages: list[Page] = []
    n = r.overview_rows
    chunks = [games[i : i + n] for i in range(0, len(games), n)]
    for i, chunk in enumerate(chunks):
        live = any(g.state == "live" for g in chunk)
        pages.append(Page("overview", hold, r.overview(chunk, now, i + 1, len(chunks), tomorrow), live))
        for g in chunk:
            pages.append(_game_page(r, g, now, hold, live_hold, tomorrow))
    return pages


def _game_page(r: Renderer, g: Game, now: datetime, hold: int, live_hold: int, tomorrow: bool = False) -> Page:
    live = g.state == "live"
    return Page("game", live_hold if live else hold, r.game(g, now, tomorrow), live)


def _parse_hm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def brightness(cfg: Config, now: datetime, any_live: bool) -> int:
    b = cfg.raw["brightness"]
    start, end, t = _parse_hm(b["night_start"]), _parse_hm(b["night_end"]), now.time()
    night = (start <= t or t < end) if start > end else (start <= t < end)
    if night and not any_live:
        return int(b["night"])
    return int(b["day"])
