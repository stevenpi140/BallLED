"""有比賽進行中時只輪播已開打的比賽和賽程表；F1 進行中時固定在 F1。"""

from dataclasses import replace
from datetime import datetime

from scoreboard import playlist
from scoreboard.config import Config
from scoreboard.demo import demo_games
from scoreboard.render import Renderer

CFG = Config.load(None)
R = Renderer(128, 64)
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=CFG.tz)


def _kinds(games):
    return [p.kind for p in playlist.build(R, CFG, games, NOW)]


def _no_f1():
    return [g for g in demo_games(CFG) if g.league != "F1"]  # 2 場進行中、2 場未開打、1 場終場


def test_live_skips_games_not_started():
    # 總覽（前 4 場）→ 2 場進行中 → 總覽（第 5 場）→ 1 場終場；未開打的 2 場沒有單場頁
    assert _kinds(_no_f1()) == ["overview", "game", "game", "overview", "game"]


def test_no_live_keeps_full_rotation():
    games = [replace(g, state="pre") for g in _no_f1()]
    assert _kinds(games).count("game") == 5


def test_f1_live_pins_f1():
    pages = playlist.build(R, CFG, demo_games(CFG), NOW)
    assert [p.kind for p in pages] == ["game"] and pages[0].live


def test_f1_not_live_does_not_pin():
    games = [replace(g, state="final") if g.league == "F1" else g for g in demo_games(CFG)]
    assert len(_kinds(games)) > 1


def test_full_shows_everything():
    pages = playlist.build(R, CFG, demo_games(CFG), NOW, full=True)
    assert [p.kind for p in pages].count("game") == 6
