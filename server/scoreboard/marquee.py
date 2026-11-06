"""整點跑馬燈：每個整點讓畫面從右到左跑一次「15點了!!」。

時段沿用整點報時（chime）的時間表，但不需要喇叭，chime.enabled 沒打開也會跑。
運作方式和報時一樣：
- 每次看板來抓畫面，回應標頭帶「距離下一次跑馬燈還有幾毫秒」（X-Marquee-In）和編號（X-Marquee-Id）
- 時間到時，看板向 /marquee.bin?id=編號 下載一整條「左右各留一個畫面寬空白」的長圖，
  在看板上自己一格一格往左捲動，捲動才會順（不用每一格都向伺服器要一張圖）
"""

from __future__ import annotations

import colorsys
from datetime import datetime

from PIL import Image, ImageDraw

from .chime import GRACE, Chime
from .config import Config
from .render import Renderer


class Marquee:
    def __init__(self, cfg: Config, chime: Chime, renderer: Renderer):
        self.cfg = cfg
        self.chime = chime
        self.renderer = renderer
        self._test_at: datetime | None = None

    @property
    def conf(self) -> dict:
        return self.cfg.raw.get("marquee") or {}

    @property
    def enabled(self) -> bool:
        return bool(self.conf.get("enabled"))

    def next_at(self, now: datetime) -> datetime | None:
        if self._test_at and now - self._test_at < GRACE:
            return self._test_at
        return self.chime.next_scheduled(now)

    def test(self, now: datetime):
        """看板下一次來抓畫面時馬上跑一次。"""
        self._test_at = now

    def headers(self, now: datetime) -> dict[str, str]:
        if not self.enabled:
            return {}
        t = self.next_at(now)
        if t is None:
            return {}
        ms = max(0, int((t - now).total_seconds() * 1000))
        if ms > 3600_000:
            return {}
        return {"X-Marquee-In": str(ms), "X-Marquee-Id": t.strftime("%Y%m%d%H%M%S")}

    def text_for(self, marquee_id: str) -> str:
        """編號是 YYYYmmddHHMMSS，取出小時套進設定的文字。"""
        try:
            hour = datetime.strptime(marquee_id, "%Y%m%d%H%M%S").hour
        except ValueError:
            hour = datetime.now(self.cfg.tz).hour
        tmpl = str(self.conf.get("text") or "{hour}點了!!")
        return tmpl.format(hour=hour, hour12=hour % 12 or 12)

    def strip(self, marquee_id: str) -> tuple[Image.Image, dict[str, str]]:
        """回傳長圖與給看板的參數（每步毫秒數、跑幾遍）。"""
        img = render_strip(self.renderer, self.text_for(marquee_id), self.conf.get("color", "rainbow"))
        params = {
            "X-Marquee-Step-Ms": str(max(5, int(self.conf.get("step_ms", 15)))),
            "X-Marquee-Repeat": str(max(1, min(10, int(self.conf.get("repeat", 2))))),
        }
        return img, params


def render_strip(r: Renderer, text: str, color="rainbow", scale: int = 0) -> Image.Image:
    """用 16px 大字放大畫出文字（scale=0：畫面高度放得下的最大倍數，最多 3 倍），左右各補一個畫面寬的空白。"""
    font = r.large
    probe = ImageDraw.Draw(Image.new("1", (1, 1)))
    probe.fontmode = "1"
    x0, y0, x1, y1 = probe.textbbox((0, 0), text, font=font)
    tw, th = max(1, x1 - x0), max(1, y1 - y0)

    mask = Image.new("1", (tw, th), 0)
    d = ImageDraw.Draw(mask)
    d.fontmode = "1"
    d.text((-x0, -y0), text, font=font, fill=1)
    if scale <= 0:
        scale = min(3, r.h // th)
    scale = max(1, min(scale, r.h // th))
    mask = mask.resize((tw * scale, th * scale), Image.NEAREST)

    fill = Image.new("RGB", mask.size)
    if color == "rainbow" or not isinstance(color, (list, tuple)):
        w = mask.size[0]
        px = fill.load()
        for x in range(w):
            rr, gg, bb = colorsys.hsv_to_rgb(x / max(1, w), 1.0, 1.0)
            c = (int(rr * 255), int(gg * 255), int(bb * 255))
            for y in range(mask.size[1]):
                px[x, y] = c
    else:
        fill.paste(tuple(int(v) for v in color[:3]), (0, 0, *mask.size))

    out = Image.new("RGB", (r.w * 2 + mask.size[0], r.h), (0, 0, 0))
    out.paste(fill, (r.w, (r.h - mask.size[1]) // 2), mask)
    return out
