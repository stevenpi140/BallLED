"""F1：ESPN 公開 scoreboard JSON（免金鑰，非官方文件化）。

GET .../racing/f1/scoreboard?dates=YYYYMMDD 會回傳那個週末的大獎賽，每個賽段（FP1、FP2、FP3、
Qual、Sprint、Race）是一個 competition：
  status.type.state   pre / in / post
  status.period       目前（或最後）圈數；正賽結束時就是總圈數
  competitors[].order 名次（賽段開始前沒有）；winner 是否為冠軍
  competitors[].athlete.fullName 車手全名
ESPN 不給車隊和時間差，車隊用 teams.F1_DRIVER_TEAM 對照，正賽總圈數用 teams.F1_GP 對照。

config.yaml 有填 leagues.f1.live_token（自己 F1 帳號的 subscriptionToken）時，賽段進行中會另外連
F1 官方即時計時（f1_live.py），補上和第一名的時間差、官方車隊顏色與總圈數；連不上就只用 ESPN。
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import httpx

from ..config import Config
from ..models import Driver, Game, Race, TeamLine
from ..teams import F1_SESSIONS, f1_driver_code, f1_gp, f1_team, hex_rgb, led_color
from .base import parse_iso, to_int, us_dates_for
from .f1_live import LiveTiming

URL = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
DEFAULT_SESSIONS = ["race", "sprint", "qualifying", "sprint_qualifying"]
WHITE, YELLOW = (235, 235, 240), (255, 210, 60)


class F1:
    key = "f1"
    name = "F1"

    def __init__(self):
        self.live: LiveTiming | None = None
        self._last_pre_refresh = 0.0

    async def fetch(self, client: httpx.AsyncClient, cfg: Config, day: date) -> list[Game]:
        events: list[dict] = []
        for d in us_dates_for(day):
            r = await client.get(URL, params={"dates": d.strftime("%Y%m%d")})
            r.raise_for_status()
            events += r.json().get("events", [])
        games = parse({"events": events}, cfg, day)
        path = getattr(cfg, "path", None) or os.environ.get("SCOREBOARD_CONFIG") or "config.yaml"
        await self._pre_refresh_if_needed(cfg, games, path)
        self._use_live(cfg, games, config_path=path)
        return games

    async def _pre_refresh_if_needed(self, cfg: Config, games: list[Game], config_path: str):
        """賽段快開始或進行中，且 token 快過期／已過期時：自動開一次 debug Chrome → 更新 → 關掉。"""
        if os.environ.get("BALLLED_F1_AUTO_REFRESH", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if time.time() - self._last_pre_refresh < 120:  # 兩分鐘內不要重複開
            return
        now = datetime.now(cfg.tz)
        activeish = any(
            g.state == "live" or (g.state == "pre" and g.start - now < timedelta(minutes=30))
            for g in games
        )
        if not activeish:
            return
        from .f1_live import token_expiry

        token = str(cfg.league("f1").get("live_token") or "").strip()
        exp = token_expiry(token) if token else None
        # 已過期，或 10 分鐘內到期：先換再連，避免正賽進行中才 401
        if exp is not None and exp > time.time() + 10 * 60:
            return
        self._last_pre_refresh = time.time()
        try:
            from .f1_chrome import refresh_live_token
            import asyncio
            cdp_port = int(os.environ.get("BALLLED_F1_CDP_PORT", "9222") or 9222)
            loop = asyncio.get_running_loop()
            new_token = await loop.run_in_executor(
                None,
                lambda: refresh_live_token(config_path, port=cdp_port, launch=True, close_when_done=True),
            )
            if new_token:
                cfg.raw.setdefault("leagues", {}).setdefault("f1", {})["live_token"] = new_token
                if self.live is not None:
                    self.live.token = new_token
        except Exception as e:
            # 失敗就維持現況；live 連線時 negotiate 401 還會再試一次
            import logging
            logging.getLogger("scoreboard").info("F1 token 賽前自動更新略過：%s: %s", type(e).__name__, e)

    def _use_live(self, cfg: Config, games: list[Game], config_path: str | None = None):
        token = str(cfg.league("f1").get("live_token") or "").strip()
        if not token:
            return
        path = config_path or os.environ.get("SCOREBOARD_CONFIG") or "config.yaml"
        cdp_port = int(os.environ.get("BALLLED_F1_CDP_PORT", "9222") or 9222)
        if self.live is None:
            self.live = LiveTiming(token, config_path=path, cdp_port=cdp_port, auto_refresh=True)
        elif self.live.token != token:
            # config 被改過（例如自動更新寫回）：換成新 token，並保留自動更新設定
            self.live.token = token
            self.live.config_path = path
            self.live.cdp_port = cdp_port
            self.live.auto_refresh = True
        now = datetime.now(cfg.tz)
        active = [g for g in games if g.state == "live" or (g.state == "pre" and g.start - now < timedelta(minutes=20))]
        if active:
            self.live.want()  # 賽段進行中（或 20 分鐘內開始）才連線
        if not self.live.fresh:
            return
        for g in active:
            if g.state == "live" and self.live.matches(g.home.code):
                enrich(g, self.live.board())


def enrich(g: Game, board: dict):
    """用官方即時計時的資料覆蓋 ESPN 的名次：多了時間差、官方車隊顏色與總圈數。"""
    drivers = []
    for row in board.get("drivers", [])[:10]:
        team, color = f1_team(row["team"], row["tla"])
        if row["colour"]:
            try:
                color = led_color(hex_rgb("#" + row["colour"].lstrip("#")))
            except ValueError:
                pass
        gap = row["gap"]
        if gap.upper().startswith("LAP"):  # 第一名的 GapToLeader 是「LAP 34」
            gap = ""
        drivers.append(Driver(row["pos"], row["tla"], team, color, gap))
    if drivers:
        g.race.drivers = drivers
    if board.get("lap"):
        g.race.lap = int(board["lap"])
    if board.get("laps"):
        g.race.laps = int(board["laps"])
    g.extra["source"] = "f1-livetiming"


def parse(data: dict, cfg: Config, day: date) -> list[Game]:
    tz = cfg.tz
    wanted = set(cfg.league("f1").get("sessions") or DEFAULT_SESSIONS)
    games: list[Game] = []
    seen: set[str] = set()
    for ev in data.get("events", []):
        gp, gp_short, gp_laps = f1_gp(str(ev.get("name") or ev.get("shortName") or ""))
        for c in ev.get("competitions", []):
            cid = str(c.get("id"))
            abbr = str((c.get("type") or {}).get("abbreviation") or "")
            session, session_short, key = F1_SESSIONS.get(abbr, (abbr, abbr[:2], abbr.lower()))
            if cid in seen or key not in wanted:
                continue
            try:
                start = parse_iso(str(c.get("startDate") or c.get("date")), tz)
            except ValueError:
                continue
            if start.date() != day:
                continue
            seen.add(cid)
            st = c.get("status") or {}
            state = {"pre": "pre", "in": "live", "post": "final"}.get((st.get("type") or {}).get("state"), "pre")
            if (st.get("type") or {}).get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED"):
                state = "postponed"
            drivers = []
            ranked = [x for x in c.get("competitors", []) if to_int(x.get("order"))]
            for x in sorted(ranked, key=lambda x: to_int(x.get("order")))[:10]:
                code = f1_driver_code(str((x.get("athlete") or {}).get("fullName") or ""))
                team, color = f1_team("", code)
                drivers.append(Driver(to_int(x["order"]), code, team, color))
            lap = to_int(st.get("period")) or None
            race = Race(gp=gp, session=session, lap=lap if state != "pre" else None,
                        laps=gp_laps if key == "race" else None, drivers=drivers)
            if state == "final" and key == "race" and lap:
                race.laps = lap  # 結束時 period 就是實際完成的圈數
            games.append(Game(
                league="F1", id=cid, start=start, state=state,  # type: ignore[arg-type]
                # 總覽頁借用 away / home：左邊分站、右邊賽段
                away=TeamLine("F1", gp_short, gp, WHITE), home=TeamLine(key, session_short, session, YELLOW),
                race=race, detail=str((st.get("type") or {}).get("description") or ""),
            ))
    return games
