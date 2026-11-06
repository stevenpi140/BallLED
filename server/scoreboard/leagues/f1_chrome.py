"""用專用 Chrome debug profile 透過 CDP 讀 formula1.com 的 login-session，寫回 config.yaml。

Chrome 136+ 禁止對「預設個人檔案」開 remote-debugging-port，所以不能直接接你平常的 Chrome。
做法：用獨立的 --user-data-dir（預設 %LOCALAPPDATA%/BallLED/ChromeF1Debug）啟動 Chrome，
只需第一次登入 formula1.com；之後這個模組會自動用 DevTools HTTP API + WebSocket 讀 cookie。

預設流程是「用完就關」：啟動專用 profile → 讀 cookie → 寫回 config → 關掉這個 Chrome。
公式1 登入狀態會存在 profile 裡，重開時通常不用再登入。
只會讀 www.formula1.com 的 login-session，不會碰其他網域、密碼或歷史紀錄。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from .f1_live import extract_token, set_config_token, token_expiry

log = logging.getLogger("scoreboard")

F1_ORIGINS = ("https://www.formula1.com", "https://formula1.com")
COOKIE_NAME = "login-session"
DEFAULT_PORT = 9222


class ChromeTokenError(RuntimeError):
    pass


def default_profile_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "BallLED" / "ChromeF1Debug"


def chrome_candidates() -> list[Path]:
    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    rels = [
        Path("Google/Chrome/Application/chrome.exe"),
        Path("Google/Chrome Beta/Application/chrome.exe"),
        Path("Google/Chrome Canary/Application/chrome.exe"),
        Path("Chromium/Application/chrome.exe"),
    ]
    out: list[Path] = []
    for root in roots:
        if not root:
            continue
        for rel in rels:
            p = Path(root) / rel
            if p.exists():
                out.append(p)
    return out


def find_chrome() -> Path:
    candidates = chrome_candidates()
    if candidates:
        return candidates[0]
    raise ChromeTokenError(
        "找不到 Chrome。請安裝 Chrome，或設定環境變數 BALLLED_CHROME 指到 chrome.exe"
    )


def cdp_base(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}"


def cdp_reachable(port: int = DEFAULT_PORT, timeout: float = 0.8) -> bool:
    try:
        r = httpx.get(f"{cdp_base(port)}/json/version", timeout=timeout, trust_env=False)
        return r.status_code == 200
    except Exception:
        return False


def ensure_debug_chrome(port: int = DEFAULT_PORT, profile: Path | None = None,
                        open_f1: bool = True) -> Path:
    """確保專用 debug Chrome 在跑；沒跑就啟動。回傳 profile 路徑。"""
    profile = profile or Path(os.environ.get("BALLLED_F1_CHROME_PROFILE") or default_profile_dir())
    profile.mkdir(parents=True, exist_ok=True)
    if cdp_reachable(port):
        return profile

    chrome_env = os.environ.get("BALLLED_CHROME")
    chrome = Path(chrome_env) if chrome_env else find_chrome()
    args = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--remote-allow-origins=*",
    ]
    if open_f1:
        args.append("https://www.formula1.com/")
    log.info("啟動專用 F1 debug Chrome（profile：%s，port：%d）", profile, port)
    subprocess.Popen(args, close_fds=True)  # noqa: S603 - 啟動使用者自己的 Chrome

    for _ in range(40):  # 最多等 20 秒
        if cdp_reachable(port):
            return profile
        time.sleep(0.5)
    raise ChromeTokenError(
        f"Chrome 已啟動但 CDP 還連不上（{cdp_base(port)}）。"
        "請確認沒有殘留鎖死的 Chrome，或改用較大的 --cdp-port"
    )


def _wait_for_page_ws(port: int, timeout: float = 15.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{cdp_base(port)}/json/list", timeout=1.0, trust_env=False)
            targets = r.json() if r.status_code == 200 else []
        except Exception:
            targets = []
        pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        for t in pages:
            url = str(t.get("url") or "")
            if "formula1.com" in url:
                return str(t["webSocketDebuggerUrl"])
        if pages:
            return str(pages[0]["webSocketDebuggerUrl"])
        # 沒有分頁就開一個 target
        try:
            r = httpx.put(f"{cdp_base(port)}/json/new?https://www.formula1.com/", timeout=2.0, trust_env=False)
            if r.status_code in (200, 201) and r.json().get("webSocketDebuggerUrl"):
                return str(r.json()["webSocketDebuggerUrl"])
        except Exception:
            pass
        time.sleep(0.5)
    raise ChromeTokenError("找不到可附加的 formula1.com 分頁。請確認 debug Chrome 已開啟並登入")


def _cdp_call(ws_url: str, method: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    import asyncio

    async def _run():
        import websockets

        async with websockets.connect(ws_url, max_size=2 ** 22, open_timeout=timeout) as ws:
            await ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout)
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    return msg

    msg = asyncio.run(_run())
    if "error" in msg:
        err = msg["error"]
        raise ChromeTokenError(f"CDP {method} 失敗：{err.get('message') or err}")
    return msg.get("result") or {}


def fetch_login_session_cookie(port: int = DEFAULT_PORT) -> str:
    """從已啟動的 debug Chrome 讀 formula1.com 的 login-session cookie 值。"""
    ws = _wait_for_page_ws(port)
    result = _cdp_call(ws, "Network.getCookies", {"urls": list(F1_ORIGINS)})
    cookies = result.get("cookies") or []
    for c in cookies:
        if str(c.get("name") or "") == COOKIE_NAME and c.get("value"):
            return str(c["value"])
    # 有些版本回傳 Storage.getCookies
    try:
        result = _cdp_call(ws, "Storage.getCookies", {})
        cookies = result.get("cookies") or []
        for c in cookies:
            if str(c.get("name") or "") == COOKIE_NAME and c.get("value"):
                return str(c["value"])
    except ChromeTokenError:
        pass
    raise ChromeTokenError(
        "讀到 formula1.com 的 cookie，但沒有 login-session。"
        "請先在這個專用 Chrome 登入 formula1.com，重新整理後再試"
    )


def browser_ws_url(port: int = DEFAULT_PORT, timeout: float = 2.0) -> str | None:
    try:
        r = httpx.get(f"{cdp_base(port)}/json/version", timeout=timeout, trust_env=False)
        if r.status_code == 200:
            return str(r.json().get("webSocketDebuggerUrl") or "") or None
    except Exception:
        pass
    return None


def close_debug_chrome(port: int = DEFAULT_PORT, timeout: float = 5.0) -> bool:
    """透過 CDP 關閉專用 debug Chrome（只關這個 port 的實例）。"""
    ws = browser_ws_url(port, timeout=timeout)
    if not ws:
        return False
    try:
        _cdp_call(ws, "Browser.close", {}, timeout=timeout)
    except Exception as e:
        log.info("CDP Browser.close 失敗：%s", e)
        return False
    # 等它真的關掉
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not cdp_reachable(port, timeout=0.5):
            return True
        time.sleep(0.2)
    return not cdp_reachable(port, timeout=0.5)


def refresh_live_token(config_path: str | Path, port: int = DEFAULT_PORT,
                       profile: Path | None = None, launch: bool = True,
                       close_when_done: bool = True) -> str:
    """啟動（或沿用）debug Chrome → 讀 login-session → 寫進 config.yaml → 預設關掉 Chrome。回傳新 token。"""
    started_here = False
    if launch:
        if cdp_reachable(port):
            started_here = False  # 已經有使用者／之前開著的 debug Chrome：讀完不強制關
            ensure_debug_chrome(port=port, profile=profile, open_f1=False)
        else:
            ensure_debug_chrome(port=port, profile=profile, open_f1=True)
            started_here = True
    elif not cdp_reachable(port):
        raise ChromeTokenError(f"CDP 連不上 {cdp_base(port)}，請先啟動 debug Chrome")

    try:
        raw = fetch_login_session_cookie(port)
        token = extract_token(raw)
        if not token:
            raise ChromeTokenError("login-session 裡找不到 subscriptionToken；可能官網改版或 cookie 不完整")
        exp = token_expiry(token)
        if exp is not None and exp <= time.time():
            raise ChromeTokenError("Chrome 裡的 login-session 已過期，請在 debug Chrome 重新登入 formula1.com")
        if not set_config_token(config_path, token):
            raise ChromeTokenError(f"沒辦法寫入 {config_path}（檔案不存在或沒有 f1 區段）")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(exp)) if exp else "未知"
        log.info("已從 Chrome 更新 F1 live_token（到期 %s），寫回 %s", when, config_path)
        return token
    finally:
        # 這次是我們開的、且使用者要用完就關：關掉它，不要一直留在桌面
        if close_when_done and started_here:
            if close_debug_chrome(port):
                log.info("已關閉 F1 debug Chrome")
            else:
                log.warning("無法透過 CDP 關閉 debug Chrome（port %d）；請手動關掉那個視窗", port)


def launch_instructions(port: int = DEFAULT_PORT) -> str:
    profile = default_profile_dir()
    chrome = os.environ.get("BALLLED_CHROME") or "chrome"
    return (
        f"請用專用 profile 啟動 Chrome（只需做一次，之後自動更新）：\n"
        f'  "{chrome}" --remote-debugging-port={port} --user-data-dir="{profile}" '
        f"--no-first-run --no-default-browser-check https://www.formula1.com/\n"
        f"然後在那個視窗登入 formula1.com。之後跑：python -m scoreboard f1-token --from-chrome"
    )
