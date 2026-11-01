"""F1 官方即時計時：livetiming.formula1.com 的 SignalR Core 資料流（非公開文件化）。

思路參考 https://github.com/julesr0y/f1-livetiming-api（Apache-2.0）：
1. POST /signalrcore/negotiate 取得 connectionToken
2. 連 wss://livetiming.formula1.com/signalrcore?id=…，送 {"protocol":"json","version":1} 完成握手
3. 呼叫 Subscribe 訂閱頻道；第一個回應是完整快照，之後的 "feed" 訊息只帶有變動的欄位，要 deep merge

和參考專案不同：連線時用 BallLED 自己的 User-Agent，不偽裝成官方 App（BestHTTP）或官網（Origin）。
官方不接受這種連線的話，這個來源就不用，F1 照樣用 ESPN 的名次。

只在 F1 賽段進行中（或快要開始）時才連線，其他時間不佔用官方伺服器。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

import httpx

log = logging.getLogger("scoreboard")

HTTP_URL = "https://livetiming.formula1.com/signalrcore"
WS_URL = "wss://livetiming.formula1.com/signalrcore"
USER_AGENT = "BallLED/1.0 (personal LED scoreboard; +https://github.com/stevenpi140/BallLED)"
RS = "\x1e"  # SignalR 訊息分隔字元
CHANNELS = ["TimingData", "DriverList", "LapCount", "SessionInfo"]
KEEP_SECONDS = 15 * 60  # 最後一次需要之後，再保持連線 15 分鐘
FRESH_SECONDS = 60  # 一分鐘內有收到資料才算可用

# config 的賽段名稱 → SessionInfo.Type / Name 可能的寫法
SESSION_NAMES = {
    "race": ("race",), "qualifying": ("qualifying",), "sprint": ("sprint",),
    "sprint_qualifying": ("sprint qualifying", "sprint shootout"), "practice": ("practice",),
}


def deep_merge(target, src):
    """把差異更新併進目前的狀態（只改有傳來的欄位）。"""
    if not isinstance(src, dict):
        return src
    if isinstance(target, list):
        target = {str(i): v for i, v in enumerate(target)}
    if not isinstance(target, dict):
        target = {}
    for k, v in src.items():
        target[k] = deep_merge(target.get(k), v) if isinstance(v, dict) else v
    return target


class LiveTiming:
    def __init__(self):
        self.state: dict = {c: {} for c in CHANNELS}
        self.connected = False
        self.last = 0.0
        self.error = ""
        self._until = 0.0
        self._task: asyncio.Task | None = None

    # ---------- 連線 ----------

    async def negotiate(self, client: httpx.AsyncClient) -> tuple[str, str]:
        headers = {"User-Agent": USER_AGENT}
        r = await client.options(f"{HTTP_URL}/negotiate", headers=headers)
        cookie = ""
        m = re.search(r"AWSALBCORS=([^;]+)", r.headers.get("set-cookie", ""))
        if m:
            cookie = f"AWSALBCORS={m.group(1)}"  # 負載平衡器的連線黏著 cookie
            headers["Cookie"] = cookie
        r = await client.post(f"{HTTP_URL}/negotiate?negotiateVersion=1", headers=headers)
        if r.status_code != 200:
            raise RuntimeError(f"negotiate HTTP {r.status_code}: {r.text[:200]}")
        return r.json()["connectionToken"], cookie

    async def session(self, client: httpx.AsyncClient, stop_after_snapshot: bool = False, timeout: float = 20):
        """連一次線：握手、訂閱，持續接收直到斷線（或拿到快照就結束，診斷用）。"""
        import websockets
        from urllib.parse import quote

        token, cookie = await self.negotiate(client)
        headers = {"Cookie": cookie} if cookie else {}
        async with websockets.connect(f"{WS_URL}?id={quote(token, safe='')}", additional_headers=headers,
                                      user_agent_header=USER_AGENT, open_timeout=timeout, max_size=2 ** 24) as ws:
            await ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
            handshake = await asyncio.wait_for(ws.recv(), timeout)
            first = json.loads(str(handshake).split(RS)[0] or "{}")
            if first.get("error"):
                raise RuntimeError(f"握手被拒絕：{first['error']}")
            await ws.send(json.dumps({"type": 1, "invocationId": "0", "target": "Subscribe", "arguments": [CHANNELS]}) + RS)
            self.connected = True
            while True:
                raw = await asyncio.wait_for(ws.recv(), 30)
                for part in str(raw).split(RS):
                    if not part:
                        continue
                    msg = json.loads(part)
                    if msg.get("type") == 3 and msg.get("invocationId") == "0":
                        for ch, data in (msg.get("result") or {}).items():
                            self._apply(ch, data)
                        if stop_after_snapshot:
                            return
                    elif msg.get("type") == 1 and msg.get("target") == "feed":
                        args = msg.get("arguments") or []
                        if len(args) >= 2:
                            self._apply(args[0], args[1])
                    elif msg.get("type") == 7:  # 伺服器要求關閉
                        raise RuntimeError(f"伺服器關閉連線：{msg.get('error', '')}")

    def _apply(self, channel: str, data):
        if channel not in self.state:
            return
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                return
        self.state[channel] = deep_merge(self.state[channel], data)
        self.last = time.time()

    def want(self):
        """F1 賽段進行中時呼叫：保持連線，沒在跑就啟動背景連線。"""
        self._until = time.time() + KEEP_SECONDS
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def _loop(self):
        from ..aggregator import make_client

        async with make_client() as client:
            while time.time() < self._until:
                try:
                    await self.session(client)
                except Exception as e:
                    self.error = f"{type(e).__name__}: {e}"
                    log.info("F1 即時計時連線中斷：%s", self.error)
                finally:
                    self.connected = False
                await asyncio.sleep(10)

    @property
    def fresh(self) -> bool:
        return time.time() - self.last < FRESH_SECONDS

    # ---------- 解析 ----------

    def matches(self, session_key: str) -> bool:
        info = self.state.get("SessionInfo") or {}
        names = " ".join(str(info.get(k) or "") for k in ("Type", "Name")).lower()
        wanted = SESSION_NAMES.get(session_key, (session_key,))
        if session_key == "qualifying" and "sprint" in names:
            return False
        if session_key == "race" and "sprint" in names:
            return False
        return any(w in names for w in wanted)

    def board(self) -> dict:
        """整理成看板要的格式：圈數與依名次排序的車手（代碼、車隊、顏色、差距）。"""
        lines = (self.state.get("TimingData") or {}).get("Lines") or {}
        drivers = self.state.get("DriverList") or {}
        lap = self.state.get("LapCount") or {}
        rows = []
        for num, t in lines.items():
            if not isinstance(t, dict):
                continue
            meta = drivers.get(num) or {}
            try:
                pos = int(t.get("Position") or 0)
            except ValueError:
                pos = 0
            if pos <= 0:
                continue
            gap = t.get("GapToLeader")
            if isinstance(gap, dict):
                gap = gap.get("Value")
            if not gap:
                best = t.get("BestLapTime") or {}
                gap = best.get("Value") if isinstance(best, dict) else ""
            if t.get("Retired") or t.get("Stopped"):
                gap = "退賽"
            elif t.get("InPit"):
                gap = "進站"
            rows.append({"pos": pos, "tla": str(meta.get("Tla") or num), "team": str(meta.get("TeamName") or ""),
                         "colour": str(meta.get("TeamColour") or ""), "gap": str(gap or "")})
        rows.sort(key=lambda r: r["pos"])
        return {"lap": lap.get("CurrentLap"), "laps": lap.get("TotalLaps"), "drivers": rows,
                "session": (self.state.get("SessionInfo") or {}).get("Name"),
                "meeting": ((self.state.get("SessionInfo") or {}).get("Meeting") or {}).get("Name")}
