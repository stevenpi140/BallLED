"""把比賽資料畫成點陣圖（預設 128x64），輸出 RGB565 給 ESP32，或放大成 LED 預覽 PNG。

為什麼在伺服器畫而不是在 ESP32 畫？繁體中文字型很大，ESP32 放不下完整字集；
在伺服器用 Pillow 畫好整張圖再傳過去（128x64x2 = 16KB），韌體就能非常單純。
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import LEAGUE_COLORS, Game

WHITE = (255, 255, 255)
DIM = (110, 110, 110)
DARK = (50, 50, 50)
LIGHT = (190, 190, 190)
YELLOW = (255, 210, 60)
RED = (255, 50, 50)
GREEN = (60, 230, 90)
WEEKDAYS = "一二三四五六日"
OVERVIEW_TOP = 12  # 總覽頁標題列高度
OVERVIEW_ROW_H = 12  # 12px 字型剛好一列
RHE_X = 83  # 單場頁 H / E 小字的位置：右邊留給兩位數比分（x 104 起）和進攻三角形（x 98 起）

HERE = Path(__file__).resolve().parent.parent / "fonts"

# 依序尋找可用的點陣中文字型。(路徑 glob, 大小, ttc index)
SMALL_CANDIDATES = [
    (str(HERE / "*fusion-pixel-12px*zh_hant*.otf"), 12, 0),
    (str(HERE / "*fusion-pixel-12px*zh_hant*.ttf"), 12, 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 12, 1),  # 文泉驛點陣正黑（內嵌 12px 點陣）
    (str(HERE / "wqy-zenhei.ttc"), 12, 1),  # Windows / macOS：把字型檔放在 server/fonts/
    ("/usr/share/fonts/opentype/unifont/unifont.otf", 16, 0),
]
LARGE_CANDIDATES = [
    ("/usr/share/fonts/opentype/unifont/unifont.otf", 16, 0),  # GNU Unifont 16px
    (str(HERE / "unifont*.otf"), 16, 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 16, 1),
    (str(HERE / "wqy-zenhei.ttc"), 16, 1),
]

# 5x7 點陣數字（比分、時鐘用），每列 5 bit
DIGITS = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    " ": ["00000"] * 7,
}

# 3x5 迷你點陣字（安打 H、失誤 E 的小字用）
MINI = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
    "H": ["101", "101", "111", "101", "101"],
    "E": ["111", "100", "111", "100", "111"],
}


def _load(candidates, override: str | None, size: int | None):
    if override:
        return ImageFont.truetype(override, size or 12)
    for pattern, sz, idx in candidates:
        for path in sorted(glob.glob(pattern)):
            if os.path.exists(path):
                return ImageFont.truetype(path, size or sz, index=idx)
    return ImageFont.load_default()


@dataclass
class Page:
    kind: str  # overview / game / idle
    hold_ms: int
    image: Image.Image
    live: bool = False  # 這頁有進行中的比賽：看板停留期間會定期重抓，比分才會即時更新
    clock_msg: str | None = None  # 時鐘頁（idle / clock）的底列文字：每次請求都用當下時間重畫，秒數才會走


class Renderer:
    def __init__(self, width: int = 128, height: int = 64, fonts: dict | None = None):
        fonts = fonts or {}
        self.w, self.h = width, height
        self.small = _load(SMALL_CANDIDATES, fonts.get("small"), fonts.get("small_size"))
        self.large = _load(LARGE_CANDIDATES, fonts.get("large"), fonts.get("large_size"))
        self._ink: dict = {}

    # ---------- 基本繪圖 ----------
    def _canvas(self):
        img = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        d = ImageDraw.Draw(img)
        d.fontmode = "1"  # 不要反鋸齒，LED 上才會銳利
        return img, d

    def tw(self, text: str, font) -> int:
        return int(font.getlength(text))

    def ink_w(self, text: str, font) -> int:
        """實際畫出來的右緣（點陣字型的「Q2」「OT」會比 getlength 多 1～2px，靠右對齊時會被切掉）。"""
        key = (id(font), text)
        if key not in self._ink:
            bb = font.getmask(text, mode="1").getbbox() if text else None
            off = font.getbbox(text, mode="1")[0] if text else 0
            self._ink[key] = max(self.tw(text, font), (bb[2] + off) if bb else 0)
        return self._ink[key]

    def text(self, d, xy, text, font, fill, max_w: int | None = None, anchor_right=False):
        if max_w is not None:
            while text and self.tw(text, font) > max_w:
                text = text[:-1]
        x, y = xy
        if anchor_right:
            x -= self.ink_w(text, font)
        d.text((x, y), text, font=font, fill=fill)
        return self.tw(text, font)

    def digits(self, d, x, y, s: str, scale: int, fill, right=False) -> int:
        cw = 5 * scale + scale  # 字寬 + 間距
        width = len(s) * cw - scale
        if right:
            x -= width
        for i, ch in enumerate(s):
            rows = DIGITS.get(ch, DIGITS[" "])
            for ry, row in enumerate(rows):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        px, py = x + i * cw + rx * scale, y + ry * scale
                        d.rectangle([px, py, px + scale - 1, py + scale - 1], fill=fill)
        return width

    def mini(self, d, x, y, s: str, fill, right=False) -> int:
        """3x5 迷你字，每字寬 3、間距 1。"""
        width = len(s) * 4 - 1
        if right:
            x -= width
        for i, ch in enumerate(s):
            for ry, row in enumerate(MINI.get(ch, ["000"] * 5)):
                for rx, bit in enumerate(row):
                    if bit == "1":
                        d.point((x + i * 4 + rx, y + ry), fill=fill)
        return width

    def _overview_layout(self, rows, drop_final: bool) -> dict | None:
        """總覽頁的共用欄位位置；放不下就回傳 None。"""
        f = self.small
        show = [bool(st) and g.state != "postponed" and not (drop_final and g.state == "final")
                for g, *_, st in rows]
        st_w = max((self.ink_w(st, f) + 3 for (*_, st), on in zip(rows, show) if on), default=0)
        away_w = max((self.tw(a, f) for _, a, *_ in rows), default=0)
        home_w = max((self.tw(h, f) for _, _, h, *_ in rows), default=0)
        home_right = self.w - st_w
        lo, hi = 4 + away_w + 2, home_right - home_w - 2  # 比分可以用的範圍（左右各留 2px）
        need_l = need_r = sep_w = 0
        for *_, mid, _, _ in rows:
            left, sep, right = _split_mid(mid)
            if sep:
                need_l, need_r = max(need_l, self.tw(left, f)), max(need_r, self.tw(right, f))
                sep_w = max(sep_w, self.tw(sep, f))
            else:
                half = self.tw(mid, f) // 2 + 1
                need_l, need_r = max(need_l, half), max(need_r, half)
        if need_l + sep_w + need_r > hi - lo:
            return None
        sep_x = (lo + hi - (need_l + sep_w + need_r)) // 2 + need_l
        return {"home_right": home_right, "sep_x": sep_x, "show": show}

    # ---------- 頁面 ----------
    def overview(self, games: list[Game], now: datetime, page: int = 1, pages: int = 1,
                 tomorrow: bool = False) -> Image.Image:
        img, d = self._canvas()
        day = now + timedelta(days=1) if tomorrow else now
        title = f"{'明日' if tomorrow else '今日'} {day.month}/{day.day}({WEEKDAYS[day.weekday()]})"
        self.text(d, (1, 0), title, self.small, YELLOW)
        if pages > 1:
            self.text(d, (self.w - 1, 0), f"{page}/{pages}", self.small, DIM, anchor_right=True)
        rows = []
        for g in games[: self.overview_rows]:
            if g.state == "pre":
                mid, mid_color = g.start.strftime("%H:%M"), DIM
            elif g.race is not None and g.state != "postponed":
                # F1：中間放領先（或冠軍）車手的代碼
                lead = g.race.drivers[0] if g.race.drivers else None
                mid = lead.code if lead else "--"
                mid_color = (lead.color if lead else WHITE) if g.state == "live" else DIM
            elif g.state == "postponed":
                mid, mid_color = "延賽", DIM
            else:
                mid, mid_color = f"{g.away.score or 0}-{g.home.score or 0}", WHITE if g.state == "live" else DIM
            away = g.away.name if g.race is not None else _tiny(g.away.name)  # F1 分站名稱最多 4 字，不截短
            rows.append((g, away, _tiny(g.home.name), mid, mid_color, g.short_status()))
        # 固定欄位：每一列共用同一個「主隊右緣」和「比分中心」，上下才會對齊。
        # 放不下時先拿掉終場的「終」（比分已經變暗，看得出是終場）
        layout = self._overview_layout(rows, drop_final=False) or self._overview_layout(rows, drop_final=True)
        for i, (g, away, home, mid, mid_color, st) in enumerate(rows):
            y = OVERVIEW_TOP + i * OVERVIEW_ROW_H
            d.rectangle([0, y + 2, 1, y + 11], fill=LEAGUE_COLORS.get(g.league, WHITE))
            win_a, win_h = _winner(g)
            if layout:
                home_right, sx, show = layout["home_right"], layout["sep_x"], layout["show"][i]
            else:  # 真的放不下：退回每列各自置中
                show = g.state not in ("final", "postponed") and bool(st)
                home_right = self.w - (self.ink_w(st, self.small) + 3 if show else 0)
                aw, hw = self.tw(away, self.small), self.tw(home, self.small)
                sx = (4 + aw + home_right - hw) // 2 - self.tw(mid, self.small) // 2 + self.tw(_split_mid(mid)[0], self.small)
            self.text(d, (4, y), away, self.small, DIM if win_h else g.away.color)
            self.text(d, (home_right, y), home, self.small, DIM if win_a else g.home.color, anchor_right=True)
            left, sep, right = _split_mid(mid)
            if layout and not sep:
                sx -= self.tw(mid, self.small) // 2  # 「延賽」這種沒有分隔符號的，以中心對齊
            self.text(d, (sx, y), left, self.small, mid_color, anchor_right=True)
            sw = self.text(d, (sx, y), sep, self.small, mid_color) if sep else 0
            self.text(d, (sx + sw, y), right, self.small, mid_color)
            if show:
                self.text(d, (self.w, y), st, self.small, GREEN if g.state == "live" else DIM, anchor_right=True)
        return img

    @property
    def overview_rows(self) -> int:
        return (self.h - OVERVIEW_TOP) // OVERVIEW_ROW_H

    def game(self, g: Game, now: datetime, tomorrow: bool = False) -> Image.Image:
        if g.race is not None:
            return self.race(g, now, tomorrow)
        img, d = self._canvas()
        lc = LEAGUE_COLORS.get(g.league, WHITE)
        # 第一列：聯盟、賽事標籤、場地
        x = 1 + self.text(d, (1, 0), g.league, self.small, lc)
        if g.label:
            x += 4 + self.text(d, (x + 4, 0), g.label, self.small, YELLOW)
        if g.venue and x + 8 < self.w:
            self.text(d, (self.w - 1, 0), _short_venue(g.venue, self.w - x - 8, self), self.small, DIM, anchor_right=True)

        win_a, win_h = _winner(g)
        for row, (t, win, lose, batting) in enumerate(
            [
                (g.away, win_a, win_h, g.state == "live" and g.is_baseball and g.top is True),
                (g.home, win_h, win_a, g.state == "live" and g.is_baseball and g.top is False),
            ]
        ):
            y = 14 + row * 17
            name_color = DIM if lose else t.color
            d.rectangle([0, y, 1, y + 14], fill=t.color)
            self.text(d, (4, y - 1), t.name, self.large, name_color, max_w=66)
            if g.state == "pre":
                if t.pitcher:
                    self.text(d, (self.w - 1, y + 2), t.pitcher, self.small, YELLOW, max_w=56, anchor_right=True)
            elif t.score is not None:
                score_color = DIM if lose else WHITE
                w = self.digits(d, self.w - 2, y + 1, str(t.score), 2, score_color, right=True)
                if batting:  # 進攻方：比分左邊一個小三角形
                    bx = self.w - 2 - w - 6
                    d.polygon([(bx, y + 4), (bx, y + 10), (bx + 3, y + 7)], fill=YELLOW)
                if g.is_baseball and t.hits is not None:
                    # 安打 H、失誤 E：疊成兩行小字，固定放在兩位數比分和三角形的左邊，上下兩隊才會對齊
                    num_color = DIM if lose else LIGHT
                    for dy, letter, n in ((2, "H", t.hits), (9, "E", t.errors or 0)):
                        self.mini(d, RHE_X, y + dy, letter, DIM)
                        self.mini(d, RHE_X + 12, y + dy, str(n), num_color, right=True)

        # 底列：狀態（明日預告前面加「明日」）
        y = 51
        status = g.status_text()
        if tomorrow and g.state == "pre":
            status = "明日 " + status
        color = GREEN if g.state == "live" else (YELLOW if g.state == "pre" else DIM)
        sw = self.text(d, (1, y), status, self.small, color)
        if g.state == "pre":
            mins = int((g.start - now).total_seconds() // 60)
            if 0 < mins < 180 and not tomorrow:
                hint = f"{mins}分後" if mins < 60 else f"{mins // 60}時{mins % 60:02d}分後"
                self.text(d, (self.w - 1, y), hint, self.small, DIM, anchor_right=True)
            elif g.away.pitcher or g.home.pitcher:
                self.text(d, (self.w - 1, y), "先發", self.small, DIM, anchor_right=True)
        if g.state == "live" and g.is_baseball:
            self._outs(d, sw + 6, y + 6, g.outs)
            self._bases(d, self.w - 12, y + 1, g.bases)
        return img

    def race(self, g: Game, now: datetime, tomorrow: bool = False) -> Image.Image:
        """F1 單場頁：分站（墨西哥站）、賽段、前三名（名次、車手代碼、車隊）、狀態。"""
        img, d = self._canvas()
        r = g.race
        x = 1 + self.text(d, (1, 0), "F1", self.small, LEAGUE_COLORS.get("F1", WHITE))
        sw = self.tw(r.session, self.small)
        self.text(d, (x + 4, 0), r.gp, self.small, YELLOW, max_w=self.w - x - 10 - sw)
        self.text(d, (self.w - 1, 0), r.session, self.small, WHITE, anchor_right=True)

        rows = r.drivers[:3]
        for i, drv in enumerate(rows):
            y = 13 + i * 12
            podium = g.state == "final" and r.session in ("正賽", "衝刺賽")
            self.text(d, (1, y), str(drv.pos), self.small, YELLOW if podium and i == 0 else DIM)
            d.rectangle([12, y + 2, 13, y + 11], fill=drv.color)
            self.text(d, (17, y), drv.code, self.small, drv.color)
            if drv.gap:
                self.text(d, (self.w - 1, y), drv.gap, self.small, DIM, anchor_right=True)
            elif drv.team:
                self.text(d, (self.w - 1, y), drv.team, self.small, DIM, anchor_right=True)
        if not rows:
            msg = "名次開始後顯示" if g.state != "final" else "沒有名次資料"
            self.text(d, (1, 25), msg, self.small, DIM)

        y = 51
        color = GREEN if g.state == "live" else (YELLOW if g.state == "pre" else DIM)
        status = g.status_text()
        if tomorrow and g.state == "pre":
            status = "明日 " + status
        self.text(d, (1, y), status, self.small, color)
        if g.state == "pre" and not tomorrow:
            mins = int((g.start - now).total_seconds() // 60)
            if 0 < mins < 24 * 60:
                hint = f"{mins}分後" if mins < 60 else f"{mins // 60}時{mins % 60:02d}分後"
                self.text(d, (self.w - 1, y), hint, self.small, DIM, anchor_right=True)
        return img

    def _outs(self, d, x, cy, outs):
        if outs is None:
            return
        for i in range(3):
            fill = RED if i < outs else None
            d.ellipse([x + i * 7, cy - 2, x + i * 7 + 4, cy + 2], fill=fill, outline=RED if i < outs else DARK)

    def _bases(self, d, cx, y, bases):
        if bases is None:
            return
        r = 3
        pos = [(cx + 6, y + 7), (cx, y + 2), (cx - 6, y + 7)]  # 一、二、三壘
        for on, (bx, by) in zip(bases, pos):
            poly = [(bx, by - r), (bx + r, by), (bx, by + r), (bx - r, by)]
            if on:
                d.polygon(poly, fill=YELLOW)
            else:
                d.polygon(poly, outline=DIM)

    def idle(self, now: datetime, note: str = "", msg: str | None = None) -> Image.Image:
        """大時鐘＋日期。msg 指定底列文字（時鐘插播用）；沒指定時顯示 note 或「今日沒有追蹤的比賽」。"""
        img, d = self._canvas()
        hhmm = now.strftime("%H:%M")
        scale = 3 if self.w >= 96 else 2
        cw = len(hhmm) * 6 * scale - scale
        # 「時:分」大字，後面接小一號的「:秒」，底部對齊
        ss = now.strftime(":%S")
        ss_scale = max(1, scale - 1)
        sw = len(ss) * 6 * ss_scale - ss_scale
        gap = ss_scale
        if cw + gap + sw <= self.w:
            x = (self.w - cw - gap - sw) // 2
            self.digits(d, x, 4, hhmm, scale, WHITE)
            self.digits(d, x + cw + gap, 4 + 7 * (scale - ss_scale), ss, ss_scale, LIGHT)
        else:  # 太窄放不下秒數：只顯示時:分
            self.digits(d, (self.w - cw) // 2, 4, hhmm, scale, WHITE)
        date_s = f"{now.month}/{now.day}（{WEEKDAYS[now.weekday()]}）"
        self.text(d, ((self.w - self.tw(date_s, self.small)) // 2, 7 * scale + 9), date_s, self.small, YELLOW)
        msg = (note or "今日沒有追蹤的比賽") if msg is None else msg
        self.text(d, ((self.w - min(self.tw(msg, self.small), self.w)) // 2, self.h - 13), msg, self.small, DIM, max_w=self.w)
        return img

    # ---------- 輸出 ----------
    @staticmethod
    def rgb565(img: Image.Image) -> bytes:
        """RGB565 little-endian，ESP32 直接 memcpy 就能用。"""
        rgb = img.convert("RGB").tobytes()
        out = bytearray(len(rgb) // 3 * 2)
        for i in range(0, len(rgb) // 3):
            r, g, b = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            out[i * 2] = v & 0xFF
            out[i * 2 + 1] = v >> 8
        return bytes(out)

    @staticmethod
    def led_preview(img: Image.Image, pitch: int = 6) -> Image.Image:
        """模擬 LED 點陣的放大圖，方便沒有硬體時先看效果。"""
        w, h = img.size
        out = Image.new("RGB", (w * pitch, h * pitch), (12, 12, 12))
        d = ImageDraw.Draw(out)
        px = img.load()
        for y in range(h):
            for x in range(w):
                c = px[x, y]
                if c == (0, 0, 0):
                    c = (28, 28, 28)
                d.ellipse([x * pitch + 1, y * pitch + 1, x * pitch + pitch - 2, y * pitch + pitch - 2], fill=c)
        return out


def _winner(g: Game) -> tuple[bool, bool]:
    if g.state != "final" or g.away.score is None or g.home.score is None:
        return False, False
    return g.away.score > g.home.score, g.home.score > g.away.score


def _split_mid(mid: str) -> tuple[str, str, str]:
    """把「88-91」「19:05」拆成左、分隔符號、右；「延賽」沒有分隔符號。"""
    for sep in ("-", ":"):
        if sep in mid:
            return mid.partition(sep)
    return "", "", mid


def _tiny(name: str) -> str:
    return name[:2] if len(name) > 3 else name


VENUE_SUFFIXES = ("棒球場", "體育館", "球場", " Stadium", " Field", " Park", " Arena", " Center")


def _short_venue(v: str, max_w: int, r: Renderer) -> str:
    """場地名稱放得下就顯示，太長先去掉「棒球場」之類的字尾，還是太長就不顯示。"""
    if r.tw(v, r.small) <= max_w:
        return v
    for suffix in VENUE_SUFFIXES:
        if v.endswith(suffix):
            v = v[: -len(suffix)]
            break
    return v if r.tw(v, r.small) <= max_w else ""
