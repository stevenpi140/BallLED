"""F1：ESPN 公開 scoreboard JSON（免金鑰，非官方文件化）。

GET .../racing/f1/scoreboard?dates=YYYYMMDD 會回傳那個週末的大獎賽，每個賽段（FP1、FP2、FP3、
Qual、Sprint、Race）是一個 competition：
  status.type.state   pre / in / post
  status.period       目前（或最後）圈數；正賽結束時就是總圈數
  competitors[].order 名次（賽段開始前沒有）；winner 是否為冠軍
  competitors[].athlete.fullName 車手全名
ESPN 不給車隊和時間差，車隊用 teams.F1_DRIVER_TEAM 對照，正賽總圈數用 teams.F1_GP 對照。
"""

from __future__ import annotations

from datetime import date

import httpx

from ..config import Config
from ..models import Driver, Game, Race, TeamLine
from ..teams import F1_SESSIONS, f1_driver_code, f1_gp, f1_team
from .base import parse_iso, to_int, us_dates_for

URL = "https://site.api.espn.com/apis/site/v2/sports/racing/f1/scoreboard"
DEFAULT_SESSIONS = ["race", "sprint", "qualifying", "sprint_qualifying"]
WHITE, YELLOW = (235, 235, 240), (255, 210, 60)


class F1:
    key = "f1"
    name = "F1"

    async def fetch(self, client: httpx.AsyncClient, cfg: Config, day: date) -> list[Game]:
        events: list[dict] = []
        for d in us_dates_for(day):
            r = await client.get(URL, params={"dates": d.strftime("%Y%m%d")})
            r.raise_for_status()
            events += r.json().get("events", [])
        return parse({"events": events}, cfg, day)


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
                # 總覽頁借用 away / home：左邊大獎賽、右邊賽段
                away=TeamLine("F1", gp_short, gp, WHITE), home=TeamLine(key, session_short, session, YELLOW),
                race=race, detail=str((st.get("type") or {}).get("description") or ""),
            ))
    return games
