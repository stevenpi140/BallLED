from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

DEFAULTS = {
    "timezone": "Asia/Taipei",
    "display": {
        "width": 128,
        "height": 64,
        "page_seconds": 8,
        "live_page_seconds": 12,
        "fonts": {},  # small / large 可指定字型檔
    },
    "brightness": {"day": 160, "night": 30, "night_start": "23:30", "night_end": "07:00"},
    "poll": {"live_seconds": 2, "screen_refresh_seconds": 2, "soon_seconds": 60, "idle_seconds": 600},
    "leagues": {
        "cpbl": {"enabled": True, "teams": []},
        "mlb": {"enabled": True, "teams": []},
        "nba": {"enabled": True, "teams": []},
        "tpbl": {"enabled": True, "teams": []},
        "plg": {"enabled": True, "teams": []},
        "f1": {"enabled": True, "teams": [], "sessions": ["race", "sprint", "qualifying", "sprint_qualifying"]},
    },
    "aliases": {},
    "chime": {
        "enabled": False,
        "file": "chime.mp3",
        "volume": 70,
        "max_seconds": 20,
        "weekday": [9, 20],
        "weekend": [12, 20],
        "holidays": [],
        "workdays": [],
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Config:
    raw: dict = field(default_factory=lambda: dict(DEFAULTS))

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        data = {}
        if path and Path(path).exists():
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(_merge(DEFAULTS, data))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw["timezone"])

    @property
    def display(self) -> dict:
        return self.raw["display"]

    @property
    def aliases(self) -> dict[str, str]:
        return self.raw.get("aliases") or {}

    def league(self, key: str) -> dict:
        return self.raw["leagues"].get(key, {"enabled": False})

    def follows(self, league_key: str, *names: str) -> bool:
        """teams 為空代表該聯盟全部都看；否則任一隊的代碼 / 中文名有出現在清單即顯示。"""
        teams = [str(t).upper() for t in (self.league(league_key).get("teams") or [])]
        if not teams:
            return True
        for n in names:
            if not n:
                continue
            u = n.upper()
            if any(t == u or (len(t) >= 2 and not t.isascii() and t in u) for t in teams):
                return True
        return False
