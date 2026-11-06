"""整點報時：在設定的時段，每個整點讓看板的喇叭播放一段音檔。

時間表和音檔都放在伺服器，改設定不用重新燒錄韌體：
- 每次看板來抓畫面，回應標頭會帶「距離下一次報時還有幾毫秒」，看板自己倒數，時間一到就播放
- 音檔先轉成 48kHz、16-bit、單聲道 PCM（看板的 ES8311 音訊晶片用這個格式），
  看板下載一次存在 PSRAM，音檔或設定改了（版本不同）才會重新下載

轉檔用 ffmpeg（mp3、m4a、wav… 都可以）；沒有安裝 ffmpeg 時只能用 16-bit PCM 的 wav 檔。
"""

from __future__ import annotations

import array
import hashlib
import logging
import shutil
import subprocess
import wave
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Config

log = logging.getLogger("scoreboard")

SAMPLE_RATE = 48000
GRACE = timedelta(seconds=30)  # 整點過後 30 秒內來抓畫面的，還是馬上播（看板用報時編號避免重播）


class Chime:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pcm: bytes | None = None
        self._key: tuple | None = None
        self._test_at: datetime | None = None
        self.error = ""

    @property
    def conf(self) -> dict:
        return self.cfg.raw.get("chime") or {}

    @property
    def enabled(self) -> bool:
        return bool(self.conf.get("enabled"))

    # ---------- 時間表 ----------

    def is_holiday(self, d: date) -> bool:
        """週六日與 holidays 列出的日子是假日；workdays 列出的補班日當平日。"""
        iso = d.isoformat()
        if iso in [str(x) for x in self.conf.get("workdays") or []]:
            return False
        return d.weekday() >= 5 or iso in [str(x) for x in self.conf.get("holidays") or []]

    def hours(self, d: date) -> range:
        start, end = self.conf.get("weekend" if self.is_holiday(d) else "weekday") or (0, -1)
        return range(int(start), int(end) + 1)

    def next_chime(self, now: datetime) -> datetime | None:
        """下一次報時的時間（整點剛過 30 秒內也算，讓晚一點來抓畫面的看板還是會播）。"""
        if self._test_at and now - self._test_at < GRACE:
            return self._test_at
        return self.next_scheduled(now)

    def next_scheduled(self, now: datetime) -> datetime | None:
        """時間表上的下一個整點（不含測試），整點跑馬燈也用這個時間表。"""
        for offset in range(8):
            d = (now + timedelta(days=offset)).date()
            for h in self.hours(d):
                t = datetime(d.year, d.month, d.day, h, tzinfo=now.tzinfo)
                if t > now - GRACE:
                    return t
        return None

    def test(self, now: datetime):
        """馬上播一次（測試音量用）。"""
        self._test_at = now

    def headers(self, now: datetime) -> dict[str, str]:
        if not self.enabled or not self.pcm():
            return {}
        t = self.next_chime(now)
        if t is None:
            return {}
        ms = max(0, int((t - now).total_seconds() * 1000))
        if ms > 3600_000:  # 一小時以後的事，之後的回應會再告訴看板
            return {}
        return {
            "X-Chime-In": str(ms),
            "X-Chime-Id": t.strftime("%Y%m%d%H%M%S"),
            "X-Chime-Ver": self.version(),
            "X-Chime-Volume": str(int(self.conf.get("volume", 70))),
        }

    # ---------- 音檔 ----------

    def _source(self) -> Path:
        return Path(str(self.conf.get("file") or "chime.mp3"))

    def version(self) -> str:
        return hashlib.sha1(self.pcm() or b"").hexdigest()[:12]

    def pcm(self) -> bytes | None:
        """轉好的 PCM（48kHz、16-bit、單聲道、little-endian）。音檔沒變就沿用上次的結果。"""
        src = self._source()
        try:
            st = src.stat()
        except OSError:
            self.error = f"找不到音檔 {src.resolve()}"
            return None
        key = (str(src), st.st_mtime, st.st_size, self.conf.get("max_seconds", 20))
        if key != self._key:
            try:
                self._pcm = convert(src, float(self.conf.get("max_seconds", 20)))
                self.error = ""
            except Exception as e:
                self._pcm, self.error = None, f"音檔轉換失敗：{e}"
                log.warning("整點報時：%s", self.error)
            self._key = key
        return self._pcm


def convert(src: Path, max_seconds: float) -> bytes:
    if shutil.which("ffmpeg"):
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(src), "-t", str(max_seconds),
             "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=60,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode(errors="replace").strip()[:300])
        return r.stdout
    if src.suffix.lower() != ".wav":
        raise RuntimeError("沒有安裝 ffmpeg，只能使用 wav 檔（sudo apt install ffmpeg）")
    return _wav_to_pcm(src, max_seconds)


def _wav_to_pcm(src: Path, max_seconds: float) -> bytes:
    """不靠 ffmpeg 的備用做法：16-bit wav 轉單聲道、線性內插重新取樣成 48kHz。"""
    with wave.open(str(src)) as w:
        if w.getsampwidth() != 2:
            raise RuntimeError("wav 檔必須是 16-bit PCM")
        ch, rate = w.getnchannels(), w.getframerate()
        frames = w.readframes(min(w.getnframes(), int(rate * max_seconds)))
    s = array.array("h", frames)
    mono = [sum(s[i:i + ch]) // ch for i in range(0, len(s), ch)] if ch > 1 else list(s)
    n = int(len(mono) * SAMPLE_RATE / rate)
    out = array.array("h", bytes(2 * n))
    for i in range(n):
        p = i * rate / SAMPLE_RATE
        j = int(p)
        a, b = mono[min(j, len(mono) - 1)], mono[min(j + 1, len(mono) - 1)]
        out[i] = int(a + (b - a) * (p - j))
    return out.tobytes()
