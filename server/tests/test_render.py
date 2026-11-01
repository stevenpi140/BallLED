from datetime import datetime

from scoreboard import playlist
from scoreboard.config import Config
from scoreboard.demo import demo_games
from scoreboard.render import Renderer

CFG = Config.load(None)


def test_frame_size_and_pages():
    r = Renderer(128, 64)
    now = datetime.now(CFG.tz)
    games = demo_games(CFG)
    pages = playlist.build(r, CFG, games, now)
    # 6 場 → 2 頁總覽 + 6 頁單場
    assert [p.kind for p in pages].count("game") == 6
    assert [p.kind for p in pages].count("overview") == 2
    for p in pages:
        assert len(Renderer.rgb565(p.image)) == 128 * 64 * 2
    assert pages[0].image.getbbox() is not None  # 不是全黑


def test_idle_page():
    r = Renderer(128, 64)
    pages = playlist.build(r, CFG, [], datetime.now(CFG.tz))
    assert len(pages) == 1 and pages[0].kind == "idle"


def test_brightness_night():
    late = datetime(2026, 10, 26, 2, 0, tzinfo=CFG.tz)
    noon = datetime(2026, 10, 26, 12, 0, tzinfo=CFG.tz)
    assert playlist.brightness(CFG, late, any_live=False) == CFG.raw["brightness"]["night"]
    assert playlist.brightness(CFG, late, any_live=True) == CFG.raw["brightness"]["day"]
    assert playlist.brightness(CFG, noon, any_live=False) == CFG.raw["brightness"]["day"]


def test_many_games_each_shown_once_per_cycle():
    from dataclasses import replace
    from datetime import timedelta

    r = Renderer(128, 64)
    now = datetime(2026, 10, 26, 9, 0, tzinfo=CFG.tz)
    base = demo_games(CFG)[1]  # NBA
    games = [replace(base, id=str(i), start=now + timedelta(minutes=10 * i)) for i in range(15)]
    pages = playlist.build(r, CFG, games, now)
    kinds = [p.kind for p in pages]
    assert kinds.count("game") == 15
    assert kinds.count("overview") == 4  # 每頁 4 列
    # 總覽頁穿插在單場頁之間，而不是全部擠在最前面
    assert kinds[:6] == ["overview", "game", "game", "game", "game", "overview"]
