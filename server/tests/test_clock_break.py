"""時鐘插播：每小時 0、10、20…50 分顯示時鐘 2 分鐘。"""

from datetime import datetime

from scoreboard import playlist
from scoreboard.config import Config
from scoreboard.demo import demo_games
from scoreboard.render import Renderer

CFG = Config.load(None)
TZ = CFG.tz


def _at(h, m, s=0):
    return datetime(2026, 11, 6, h, m, s, tzinfo=TZ)


def test_windows_every_ten_minutes():
    on = [_at(9, 0), _at(9, 1, 59), _at(14, 10, 30), _at(20, 50), _at(23, 31, 0)]
    off = [_at(9, 2), _at(9, 9, 59), _at(14, 15), _at(20, 58), _at(23, 32, 1)]
    assert all(playlist.clock_break_ms(CFG, t) > 0 for t in on)
    assert all(playlist.clock_break_ms(CFG, t) == 0 for t in off)


def test_remaining_ms():
    assert playlist.clock_break_ms(CFG, _at(9, 0)) == 120_000
    assert playlist.clock_break_ms(CFG, _at(9, 1, 30)) == 30_000


def test_configurable_and_disable():
    cfg = Config({**CFG.raw, "clock_break": {"enabled": True, "every_minutes": 15, "minutes": 1}})
    assert playlist.clock_break_ms(cfg, _at(9, 15, 20)) == 40_000
    assert playlist.clock_break_ms(cfg, _at(9, 10)) == 0
    off = Config({**CFG.raw, "clock_break": {"enabled": False}})
    assert playlist.clock_break_ms(off, _at(9, 0)) == 0


def test_clock_page_summary():
    r = Renderer(128, 64)
    games = demo_games(CFG)
    page = playlist.clock_page(r, games, _at(9, 0))
    assert page.kind == "clock" and page.image.getbbox() is not None
    # 底列有摘要文字（和沒有比賽時的底列不同）
    empty = playlist.clock_page(r, [], _at(9, 0))
    assert page.image.crop((0, 50, 128, 64)).tobytes() != empty.image.crop((0, 50, 128, 64)).tobytes()
