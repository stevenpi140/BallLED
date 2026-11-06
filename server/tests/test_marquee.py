"""整點跑馬燈：時間表、回應標頭與長圖。"""

from datetime import datetime

from scoreboard.chime import Chime
from scoreboard.config import Config
from scoreboard.marquee import Marquee, render_strip
from scoreboard.render import Renderer

BASE = Config.load(None)
TZ = BASE.tz
R = Renderer(128, 64)


def _marquee(**over) -> Marquee:
    cfg = Config({**BASE.raw, "marquee": {**BASE.raw["marquee"], "enabled": True, **over}})
    return Marquee(cfg, Chime(cfg), R)


def test_disabled_sends_no_headers():
    cfg = Config(BASE.raw)
    m = Marquee(cfg, Chime(cfg), R)
    assert m.headers(datetime(2026, 10, 28, 8, 59, 50, tzinfo=TZ)) == {}


def test_headers_follow_chime_schedule_even_if_chime_disabled():
    m = _marquee()
    assert not m.chime.enabled
    h = m.headers(datetime(2026, 10, 28, 8, 59, 50, tzinfo=TZ))  # 平日 09:00 是第一次
    assert h == {"X-Marquee-In": "10000", "X-Marquee-Id": "20261028090000"}
    # 平日 20:00 之後就沒有了，下一次是隔天 09:00，超過一小時不送
    assert m.headers(datetime(2026, 10, 28, 21, 0, tzinfo=TZ)) == {}


def test_test_runs_now():
    m = _marquee()
    now = datetime(2026, 10, 28, 22, 15, 3, tzinfo=TZ)
    m.test(now)
    assert m.headers(now) == {"X-Marquee-In": "0", "X-Marquee-Id": "20261028221503"}


def test_text_for_hour():
    assert _marquee().text_for("20261028150000") == "15點了!!"
    assert _marquee(text="{hour12}點囉").text_for("20261028150000") == "3點囉"


def test_strip_size_and_blank_margins():
    img, params = _marquee().strip("20261028150000")
    assert img.height == 64
    assert img.width > 128 * 2
    # 左右各一個畫面寬是空白，捲動開始和結束時畫面是黑的
    assert img.crop((0, 0, 128, 64)).getbbox() is None
    assert img.crop((img.width - 128, 0, img.width, 64)).getbbox() is None
    assert img.crop((128, 0, img.width - 128, 64)).getbbox() is not None
    assert params == {"X-Marquee-Step-Ms": "15", "X-Marquee-Repeat": "2"}


def test_strip_solid_color():
    img = render_strip(R, "9點了!!", [255, 200, 0])
    colors = {c for _, c in img.getcolors(1 << 16)}
    assert colors == {(0, 0, 0), (255, 200, 0)}
