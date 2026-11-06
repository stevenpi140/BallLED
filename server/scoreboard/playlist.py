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
        msg = note or "今日沒有追蹤的比賽"
        return [Page("idle", 15000, r.idle(now, msg=msg), clock_msg=msg)]
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


def clock_break_ms(cfg: Config, now: datetime) -> int:
    """現在在時鐘插播時段內的話，回傳離插播結束還有幾毫秒；不在時段內回傳 0。

    預設每小時的 0、10、20、30、40、50 分開始，各顯示 2 分鐘（x0:00～x1:59）。
    """
    c = cfg.raw.get("clock_break") or {}
    if not c.get("enabled"):
        return 0
    every = max(1, int(c.get("every_minutes", 10)))
    length = max(0, min(every, int(c.get("minutes", 2))))
    into = (now.minute % every) * 60 + now.second + now.microsecond / 1e6  # 這個週期開始了幾秒
    if into >= length * 60:
        return 0
    return max(1, int((length * 60 - into) * 1000))


def clock_page(r: Renderer, games: list[Game], now: datetime, tomorrow: bool = False) -> Page:
    """時鐘插播頁：大時鐘＋日期，底列是今天（或明天）比賽的簡短摘要。"""
    live = sum(g.state == "live" for g in games)
    if live:
        msg = f"比賽進行中 {live} 場"
    elif games:
        msg = f"{'明日' if tomorrow else '今日'}共 {len(games)} 場比賽"
    else:
        msg = ""
    return Page("clock", 15000, r.idle(now, msg=msg), clock_msg=msg)


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
