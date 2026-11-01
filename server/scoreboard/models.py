"""各聯盟共用的比賽資料格式。所有聯盟的 adapter 都轉成這個格式，渲染器只看這裡。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Literal, Optional

State = Literal["pre", "live", "final", "postponed"]

LEAGUE_COLORS = {
    "CPBL": (255, 70, 70),
    "MLB": (80, 140, 255),
    "NBA": (255, 120, 40),
    "TPBL": (60, 220, 200),
    "PLG": (190, 110, 255),
    "F1": (235, 235, 240),  # 方格旗的白，和中職的紅色區分開
}

BASEBALL = {"CPBL", "MLB"}


@dataclass
class TeamLine:
    code: str  # 英文縮寫或內部代碼，用來比對追蹤清單
    name: str  # 顯示用中文短名，例如「中信」「道奇」
    full: str = ""  # 完整名稱
    color: tuple[int, int, int] = (255, 255, 255)
    score: Optional[int] = None
    pitcher: Optional[str] = None  # 棒球：先發投手
    hits: Optional[int] = None  # 棒球：安打（RHE 的 H）
    errors: Optional[int] = None  # 棒球：失誤（RHE 的 E）


@dataclass
class Driver:
    """F1 名次表的一列。"""

    pos: int
    code: str  # 三碼縮寫，例如 VER
    team: str = ""  # 車隊中文短名，例如「紅牛」
    color: tuple[int, int, int] = (255, 255, 255)
    gap: str = ""  # 與第一名的差距，例如「+1.234」「+1 圈」；排位賽是單圈時間


@dataclass
class Race:
    """F1 這類「不是兩隊對打」的比賽。"""

    gp: str  # 大獎賽名稱，例如「墨西哥」
    session: str  # 正賽 / 排位賽 / 衝刺賽 / 衝刺排位
    lap: Optional[int] = None
    laps: Optional[int] = None
    drivers: list[Driver] = field(default_factory=list)


@dataclass
class Game:
    league: str  # CPBL / MLB / NBA / TPBL / PLG
    id: str
    start: datetime  # 含時區
    state: State
    away: TeamLine
    home: TeamLine
    venue: str = ""
    label: str = ""  # 例如「總冠軍賽G6」「世界大賽G3」
    # 棒球
    inning: Optional[int] = None
    top: Optional[bool] = None  # True = 上半局
    outs: Optional[int] = None
    bases: Optional[tuple[bool, bool, bool]] = None  # 一、二、三壘有人
    # 籃球
    period: Optional[int] = None
    clock: str = ""
    # 賽車（F1）：有這個欄位時，away / home 只是總覽頁借用的文字欄位
    race: Optional[Race] = None
    # 狀態原文（adapter 無法細分時直接顯示）
    detail: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def is_baseball(self) -> bool:
        return self.league in BASEBALL

    def status_text(self) -> str:
        """給畫面用的狀態文字。"""
        if self.state == "postponed":
            # 來源給的是中文（保留、取消…）就照用，英文代碼（POSTPONED、NOT_STARTED）一律顯示「延賽」
            return self.detail if re.search(r"[\u4e00-\u9fff]", self.detail or "") else "延賽"
        if self.race is not None:
            return self._race_status()
        if self.state == "final":
            if self.is_baseball and self.inning and self.inning != 9:
                return f"終場 {self.inning}局"
            if not self.is_baseball and self.period and self.period > 4:
                return "終場 OT" if self.period == 5 else f"終場 {self.period - 4}OT"
            return "終場"
        if self.state == "pre":
            return self.start.strftime("%H:%M") + " 開打"
        # live
        if self.is_baseball and self.inning:
            return f"{self.inning}局{'上' if self.top else '下'}"
        if not self.is_baseball and self.period:
            q = f"Q{self.period}" if self.period <= 4 else ("OT" if self.period == 5 else f"{self.period - 4}OT")
            return f"{q} {self.clock}".strip()
        return self.detail or "比賽中"

    def _race_status(self) -> str:
        r = self.race
        if self.state == "pre":
            return self.start.strftime("%H:%M") + " 開始"
        if self.state == "final":
            return f"{r.session}結束"
        if r.lap and r.laps:
            return f"第 {r.lap}/{r.laps} 圈"
        return self.detail or "進行中"

    def short_status(self) -> str:
        """總覽頁用的極短狀態（最多約 3 個半形字寬）。"""
        if self.state == "postponed":
            return "延"
        if self.race is not None and self.state == "live":
            return f"L{self.race.lap}" if self.race.lap else "LIVE"
        if self.state == "final":
            return "終"
        if self.state == "pre":
            return ""
        if self.is_baseball and self.inning:
            return f"{self.inning}{'上' if self.top else '下'}"
        if not self.is_baseball and self.period:
            return f"Q{self.period}" if self.period <= 4 else "OT"
        return "LIVE"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["status_text"] = self.status_text()
        return d
