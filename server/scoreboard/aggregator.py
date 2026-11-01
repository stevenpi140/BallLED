"""定時向各聯盟抓資料。比賽進行中縮短間隔（預設 2 秒），沒比賽時拉長（預設 10 分鐘）。

聯盟可以用 min_live_seconds 設定自己的最短間隔（例如資料量很大的來源），
config.yaml 的 leagues.<聯盟>.live_seconds 也可以個別覆蓋。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from .config import Config
from .leagues import ALL
from .leagues.base import UA
from .models import Game

log = logging.getLogger("scoreboard")


@dataclass
class LeagueState:
    league: object
    games: list[Game] = field(default_factory=list)
    next_due: float = 0.0
    last_ok: float = 0.0
    error: str = ""


class Aggregator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.states = [LeagueState(cls()) for cls in ALL if cfg.league(cls.key).get("enabled", True)]
        self.updated_at = 0.0
        self._task: asyncio.Task | None = None

    def today(self):
        return datetime.now(self.cfg.tz).date()

    def games(self) -> list[Game]:
        # 沒有比賽的聯盟就不會出現在輪播裡，不用另外判斷季節
        out = [g for s in self.states for g in s.games]
        # 固定用開打時間排序，不因比賽狀態改變而重排，這樣輪播一圈每一場都一定會輪到
        out.sort(key=lambda g: (g.start, g.league, g.id))
        return out

    def _interval(self, lg, games: list[Game]) -> float:
        p = self.cfg.raw["poll"]
        now = datetime.now(self.cfg.tz)
        if any(g.state == "live" for g in games):
            live = self.cfg.league(lg.key).get("live_seconds", p["live_seconds"])
            return max(live, getattr(lg, "min_live_seconds", 0))
        if any(g.state == "pre" and g.start - now < timedelta(minutes=30) for g in games):
            return p["soon_seconds"]
        return p["idle_seconds"]

    def _filter(self, key: str, games: list[Game]) -> list[Game]:
        return [
            g for g in games
            if g.race is not None  # F1 這類比賽用 sessions 篩選，不看球隊
            or self.cfg.follows(key, g.away.code, g.away.name, g.away.full)
            or self.cfg.follows(key, g.home.code, g.home.name, g.home.full)
        ]

    async def refresh(self, client: httpx.AsyncClient, st: LeagueState, day=None):
        lg = st.league
        try:
            games = await lg.fetch(client, self.cfg, day or self.today())
            st.games = self._filter(lg.key, games)
            st.last_ok, st.error = time.time(), ""
            st.next_due = time.time() + self._interval(lg, st.games)
            log.info("%s：%d 場（下次 %ds 後更新）", lg.name, len(st.games), st.next_due - time.time())
        except Exception as e:  # 單一聯盟失敗不影響其他聯盟，保留上次成功的資料
            st.error = f"{type(e).__name__}: {e}"
            st.next_due = time.time() + 60
            log.warning("%s 更新失敗：%s", lg.name, st.error)
        self.updated_at = time.time()

    async def refresh_all(self, client: httpx.AsyncClient, day=None):
        await asyncio.gather(*(self.refresh(client, s, day) for s in self.states))

    async def run(self):
        day = self.today()
        async with make_client() as client:
            while True:
                if self.today() != day:  # 過午夜：換日，全部重抓
                    day = self.today()
                    for s in self.states:
                        s.games, s.next_due = [], 0
                due = [s for s in self.states if time.time() >= s.next_due]
                if due:
                    await asyncio.gather(*(self.refresh(client, s) for s in due))
                await asyncio.sleep(0.5)

    def start(self):
        self._task = asyncio.create_task(self.run())

    def status(self) -> list[dict]:
        return [
            {
                "league": s.league.name,
                "games": len(s.games),
                "last_ok": s.last_ok,
                "error": s.error,
                "next_in": max(0, round(s.next_due - time.time())),
            }
            for s in self.states
        ]


def make_client(**kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"},
        **kwargs,
    )
