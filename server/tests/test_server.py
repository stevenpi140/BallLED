"""端對端測試：用假資料跑整個 HTTP 伺服器，確認 ESP32 拿到的畫面格式正確。"""

import os

from fastapi.testclient import TestClient

os.environ["SCOREBOARD_CONFIG"] = "does-not-exist.yaml"  # 用預設設定

from scoreboard import server  # noqa: E402
from scoreboard.demo import demo_games  # noqa: E402


def _client(monkeypatch, games):
    # 不啟動背景抓資料，直接塞入比賽
    monkeypatch.setattr(server.agg, "start", lambda: None)
    monkeypatch.setattr(server.agg, "games", lambda: games)
    monkeypatch.setitem(server.cfg.raw, "clock_break", {"enabled": False})  # 時鐘插播看實際時間，另外測
    return TestClient(server.app)


def test_frame_endpoint(monkeypatch):
    games = demo_games(server.cfg)
    with _client(monkeypatch, games) as c:
        r = c.get("/frame?page=0")
        assert r.status_code == 200
        assert len(r.content) == 128 * 64 * 2
        pages = int(r.headers["X-Pages"])
        assert pages == 8  # 2 頁總覽 + 6 場
        assert int(r.headers["X-Hold-Ms"]) >= 1000
        assert 0 < int(r.headers["X-Brightness"]) <= 255
        # 頁碼超過總頁數會自動繞回
        r2 = c.get(f"/frame?page={pages}")
        assert r2.headers["X-Page"] == "0"


def test_status_and_preview(monkeypatch):
    with _client(monkeypatch, demo_games(server.cfg)) as c:
        s = c.get("/api/status").json()
        assert s["games"] == 6 and s["pages"] == 8 and s["cycle_seconds"] > 0
        assert c.get("/preview/1.png").headers["content-type"] == "image/png"
        assert "點陣看板預覽" in c.get("/").text
        assert len(c.get("/api/games").json()) == 6


def test_idle_when_no_games(monkeypatch):
    with _client(monkeypatch, []) as c:
        r = c.get("/frame")
        assert r.headers["X-Pages"] == "1"
        assert len(r.content) == 128 * 64 * 2


def test_live_pages_ask_for_refresh(monkeypatch):
    games = demo_games(server.cfg)
    with _client(monkeypatch, games) as c:
        heads = [c.get(f"/frame?page={i}").headers for i in range(8)]
    live = [h["X-Refresh-Ms"] for h in heads]
    assert "2000" in live and "0" in live  # 進行中的比賽要求每 2 秒重抓，其他頁不用


def test_chime_endpoints(monkeypatch, tmp_path):
    import wave

    from scoreboard.chime import Chime
    from scoreboard.config import Config

    wav = tmp_path / "c.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x10\x00" * 4800)  # 0.1 秒
    cfg = Config({**server.cfg.raw, "chime": {**server.cfg.raw["chime"], "enabled": True, "file": str(wav)}})
    monkeypatch.setattr(server, "chime", Chime(cfg))
    with _client(monkeypatch, demo_games(server.cfg)) as c:
        assert c.post("/api/chime/test").json()["ok"]
        h = c.get("/frame").headers
        assert h["X-Chime-In"] == "0" and h["X-Chime-Ver"]
        r = c.get("/chime.pcm")
        assert r.status_code == 200 and len(r.content) == 9600
        assert c.get("/api/status").json()["chime"]["enabled"]


def test_marquee_endpoints(monkeypatch):
    from scoreboard.chime import Chime
    from scoreboard.config import Config
    from scoreboard.marquee import Marquee

    cfg = Config({**server.cfg.raw, "marquee": {**server.cfg.raw["marquee"], "enabled": True}})
    monkeypatch.setattr(server, "marquee", Marquee(cfg, Chime(cfg), server.renderer))
    with _client(monkeypatch, demo_games(server.cfg)) as c:
        assert c.post("/api/marquee/test").json()["ok"]
        h = c.get("/frame").headers
        assert h["X-Marquee-In"] == "0" and len(h["X-Marquee-Id"]) == 14
        r = c.get("/marquee.bin", params={"id": h["X-Marquee-Id"]})
        w = int(r.headers["X-Marquee-Width"])
        assert r.status_code == 200 and len(r.content) == w * 64 * 2
        assert r.headers["X-Marquee-Step-Ms"] == "15" and r.headers["X-Marquee-Repeat"] == "2"
        assert c.get("/preview/marquee.png").status_code == 200
        assert c.get("/api/status").json()["marquee"]["enabled"]


def test_clock_break_endpoint(monkeypatch):
    from scoreboard import playlist

    with _client(monkeypatch, demo_games(server.cfg)) as c:
        monkeypatch.setitem(server.cfg.raw, "clock_break", {"enabled": True, "every_minutes": 10, "minutes": 2})
        monkeypatch.setattr(playlist, "clock_break_ms", lambda cfg, now: 45_000)
        server._cache["key"] = None
        r = c.get("/frame", params={"page": 5})
        assert r.headers["X-Pages"] == "1" and r.headers["X-Hold-Ms"] == "15000"
        assert c.get("/api/status").json()["clock_break"] is True
        monkeypatch.setattr(playlist, "clock_break_ms", lambda cfg, now: 3_000)
        server._cache["key"] = None
        assert c.get("/frame").headers["X-Hold-Ms"] == "3000"  # 快結束時只停到插播結束
        monkeypatch.setattr(playlist, "clock_break_ms", lambda cfg, now: 0)
        assert int(c.get("/frame").headers["X-Pages"]) > 1  # 插播結束回到比賽輪播


def test_clock_pages_tick_every_request(monkeypatch):
    from datetime import datetime as real_dt

    with _client(monkeypatch, []) as c:  # 沒有比賽：時鐘頁
        r1 = c.get("/frame")
        assert r1.headers["X-Refresh-Ms"] == "250"  # 看板停留期間每 0.25 秒重抓，秒數才會走
        t = [real_dt(2026, 11, 6, 9, 5, 1, tzinfo=server.cfg.tz)]

        class FakeDT(real_dt):
            @classmethod
            def now(cls, tz=None):
                return t[0]

        monkeypatch.setattr(server, "datetime", FakeDT)
        a = c.get("/frame").content
        t[0] = t[0].replace(second=2)
        b = c.get("/frame").content
        assert a != b  # 同一分鐘內，秒數不同畫面就不同
        board = c.get("/api/status").json()["board"]
        assert board["seconds_ago"] < 5
    # 有比賽、不在插播時段：比賽頁不重抓
    with _client(monkeypatch, [g for g in demo_games(server.cfg) if g.state == "final"]) as c:
        assert c.get("/frame").headers["X-Refresh-Ms"] == "0"
