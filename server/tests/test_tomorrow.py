"""今天所有比賽都結束後，改輪播明天的比賽預告。"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

from scoreboard import playlist
from scoreboard.aggregator import Aggregator
from scoreboard.config import Config
from scoreboard.demo import demo_games
from scoreboard.render import Renderer

CFG = Config.load(None)
R = Renderer(128, 64)


def _games(state: str):
    return [replace(g, state=state) for g in demo_games(CFG)]


def _agg(today_by_league: dict, tomorrow_by_league: dict) -> Aggregator:
    agg = Aggregator(CFG)
    for st in agg.states:
        st.games = today_by_league.get(st.league.name, [])
        st.tomorrow = tomorrow_by_league.get(st.league.name, [])
    return agg


def test_shows_today_while_any_game_unfinished():
    final, pre = _games("final"), _games("pre")
    agg = _agg({"NBA": [final[1]], "MLB": [replace(final[0], state="live")]}, {"NBA": [pre[1]]})
    assert not agg.showing_tomorrow()
    assert [g.league for g in agg.games()] == ["MLB", "NBA"]


def test_switches_to_tomorrow_when_all_done():
    final, pre = _games("final"), _games("pre")
    agg = _agg({"NBA": [final[1]], "PLG": [replace(final[4], state="postponed")]},
               {"NBA": [pre[1]], "CPBL": [pre[2]]})
    assert agg.showing_tomorrow()
    assert {g.league for g in agg.games()} == {"NBA", "CPBL"}
    assert all(g.state == "pre" for g in agg.games())


def test_stays_on_today_if_tomorrow_empty():
    agg = _agg({"NBA": [_games("final")[1]]}, {})
    assert not agg.showing_tomorrow()
    assert len(agg.games()) == 1


def test_tomorrow_fetched_only_for_finished_leagues(monkeypatch):
    agg = Aggregator(CFG)
    final, live = _games("final"), _games("live")
    today = {"NBA": [final[1]], "MLB": [live[0]]}
    asked = []

    async def fake_fetch(self, client, cfg, day):
        if day == agg.today():
            return today.get(self.name, [])
        asked.append(self.name)
        return []

    for st in agg.states:
        monkeypatch.setattr(type(st.league), "fetch", fake_fetch)

    async def one_round():
        await agg.refresh_all(None)
        for st in agg.states:
            if st.last_ok and st.day_done:
                await agg.refresh_tomorrow(None, st)

    asyncio.run(one_round())
    assert "NBA" in asked and "MLB" not in asked


def test_tomorrow_pages_say_tomorrow():
    now = datetime(2026, 11, 6, 22, 0, tzinfo=CFG.tz)
    games = [replace(g, state="pre", start=now + timedelta(hours=20)) for g in demo_games(CFG)[:3]]
    pages = playlist.build(R, CFG, games, now, tomorrow=True)
    today_pages = playlist.build(R, CFG, games, now, tomorrow=False)
    assert [p.kind for p in pages] == [p.kind for p in today_pages]
    # 總覽頁標題（第一列）和單場頁底列都和「今日」版本不同
    assert pages[0].image.crop((0, 0, 128, 12)).tobytes() != today_pages[0].image.crop((0, 0, 128, 12)).tobytes()
    assert pages[1].image.crop((0, 51, 128, 64)).tobytes() != today_pages[1].image.crop((0, 51, 128, 64)).tobytes()
