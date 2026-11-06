"""F1 官方即時計時：livetiming.formula1.com 的 SignalR Core 資料流（非公開文件化）。

思路參考 https://github.com/julesr0y/f1-livetiming-api（Apache-2.0）：
1. POST /signalrcore/negotiate 取得 connectionToken
2. 連 wss://livetiming.formula1.com/signalrcore?id=…，送 {"protocol":"json","version":1} 完成握手
3. 呼叫 Subscribe 訂閱頻道；第一個回應是完整快照，之後的 "feed" 訊息只帶有變動的欄位，要 deep merge

和參考專案不同：連線時用 BallLED 自己的 User-Agent，不偽裝成官方 App（BestHTTP）或官網（Origin）。
官方要求登入時，用你自己 F1 帳號的 subscriptionToken（config.yaml 的 leagues.f1.live_token），
以 Authorization: Bearer 送出；token 大約幾天會過期。過期時若啟用自動更新，會從專用 debug Chrome
讀 formula1.com 的 login-session 重新寫入 config.yaml（見 f1_chrome.py）；否則看板退回 ESPN 名次。
官方不接受連線的話，這個來源就不用，F1 照樣用 ESPN 的名次。

只在 F1 賽段進行中（或快要開始）時才連線，其他時間不佔用官方伺服器。
"""

from __future__ import annotations

import asyncio
import base64
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


def token_expiry(token: str) -> float | None:
    """讀 JWT 裡的到期時間（不驗證簽章，只用來提醒何時要換 token）。"""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(data["exp"])
    except Exception:
        return None


JWT_RE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")


def extract_token(text: str) -> str | None:
    """從瀏覽器複製的 login-session cookie 值（網址編碼過的 JSON）或 token 本身，取出 subscriptionToken。"""
    from urllib.parse import unquote

    s = text.strip().strip('"').strip("'")
    for _ in range(3):  # cookie 值可能被編碼不只一次
        m = re.search(r'"subscriptionToken"\s*:\s*"([^"]+)"', s)
        if m:
            return m.group(1)
        s2 = unquote(s)
        if s2 == s:
            break
        s = s2
    m = JWT_RE.search(s)
    return m.group(0) if m else None


def set_config_token(path, token: str) -> bool:
    """把 token 寫進 config.yaml 的 leagues.f1.live_token（保留檔案裡其他內容與註解）。"""
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    line = re.compile(r'^(\s*live_token:\s*).*$', re.M)
    if line.search(text):
        text = line.sub(lambda m: f'{m.group(1)}"{token}"', text, count=1)
    else:
        f1 = re.search(r"^(\s*)f1:\s*$", text, re.M)
        if not f1:
            return False
        indent = f1.group(1) + "  "
        text = text[: f1.end()] + f'\n{indent}live_token: "{token}"' + text[f1.end():]
    p.write_text(text, encoding="utf-8")
    return True


class LiveTiming:
    def __init__(self, token: str = "", *, config_path: str | None = None,
                 cdp_port: int = 9222, auto_refresh: bool = True):
        self.token = token.strip()
        self.config_path = config_path
        self.cdp_port = cdp_port
        self.auto_refresh = auto_refresh
        self._refresh_lock = asyncio.Lock()
        self._last_refresh_try = 0.0
        self.state: dict = {c: {} for c in CHANNELS}
        self.connected = False
        self.last = 0.0
        self.error = ""
        self._until = 0.0
        self._task: asyncio.Task | None = None

    # ---------- 連線 ----------

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def negotiate(self, client: httpx.AsyncClient) -> tuple[str, str]:
        headers = {"User-Agent": USER_AGENT, **self._auth()}
        r = await client.options(f"{HTTP_URL}/negotiate", headers=headers)
        cookie = ""
        m = re.search(r"AWSALBCORS=([^;]+)", r.headers.get("set-cookie", ""))
        if m:
            cookie = f"AWSALBCORS={m.group(1)}"  # 負載平衡器的連線黏著 cookie
            headers["Cookie"] = cookie
        r = await client.post(f"{HTTP_URL}/negotiate?negotiateVersion=1", headers=headers)
        if r.status_code in (401, 403):
            if await self._maybe_refresh_from_chrome():
                headers = {"User-Agent": USER_AGENT, **self._auth()}
                if cookie:
                    headers["Cookie"] = cookie
                r = await client.post(f"{HTTP_URL}/negotiate?negotiateVersion=1", headers=headers)
        if r.status_code in (401, 403):
            raise RuntimeError(f"negotiate HTTP {r.status_code}：官方要求登入，或 token 已過期（{r.text[:120]}）")
        if r.status_code != 200:
            raise RuntimeError(f"negotiate HTTP {r.status_code}: {r.text[:200]}")
        return r.json()["connectionToken"], cookie

    async def _maybe_refresh_from_chrome(self) -> bool:
        """401/403 時，從專用 debug Chrome 重新讀 login-session 並寫回 config.yaml。"""
        if not self.auto_refresh or not self.config_path:
            return False
        if time.time() - self._last_refresh_try < 60:  # 一分鐘內不要連續開 Chrome
            return False
        async with self._refresh_lock:
            if time.time() - self._last_refresh_try < 60:
                return False
            self._last_refresh_try = time.time()
            old = self.token
            try:
                from .f1_chrome import refresh_live_token
                loop = asyncio.get_running_loop()
                new_token = await loop.run_in_executor(None, lambda: refresh_live_token(
                    self.config_path, port=self.cdp_port, launch=True))
            except Exception as e:
                log.warning("F1 token 自動更新失敗：%s: %s", type(e).__name__, e)
                self.error = f"token 自動更新失敗：{e}"
                return False
            if new_token and new_token != old:
                self.token = new_token
                log.info("F1 live_token 已從 Chrome 自動更新")
                return True
            return False

    async def session(self, client: httpx.AsyncClient, stop_after_snapshot: bool = False, timeout: float = 20):
        """連一次線：握手、訂閱，持續接收直到斷線（或拿到快照就結束，診斷用）。"""
        import websockets
        from urllib.parse import quote

        token, cookie = await self.negotiate(client)
        headers = {**({"Cookie": cookie} if cookie else {}), **self._auth()}
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

    def status(self) -> dict:
        exp = token_expiry(self.token) if self.token else None
        return {"token": bool(self.token), "token_expires": exp, "connected": self.connected,
                "last": self.last, "error": self.error}

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
