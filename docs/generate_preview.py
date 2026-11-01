"""產生 README 用的預覽圖 docs/preview.png（假資料，不需要網路）。

    python docs/generate_preview.py

排成 3 欄 × 2 列：總覽、MLB 比賽中、NBA 比賽中、中職賽前、中職終場（含 RHE）、F1 正賽中。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from scoreboard.config import Config  # noqa: E402
from scoreboard.demo import _tw, demo_games  # noqa: E402
from scoreboard.models import Game  # noqa: E402
from scoreboard.render import Renderer  # noqa: E402
from scoreboard.teams import CPBL_TEAMS  # noqa: E402

PITCH = 5  # 每顆 LED 放大成 5x5 像素（H / E 小字才看得清楚）
GAP = 8
COLS = 3


def main():
    cfg = Config.load(None)
    now = datetime.now(cfg.tz).replace(second=0, microsecond=0)
    games = demo_games(cfg)
    away, home = _tw(CPBL_TEAMS, "中信兄弟", 0), _tw(CPBL_TEAMS, "味全龍", 6)
    away.hits, away.errors, home.hits, home.errors = 2, 0, 12, 0
    final = Game("CPBL", "d6", now - timedelta(hours=4), "final", away, home,
                 venue="大巨蛋", label="台灣大賽G6", inning=9)

    r = Renderer(cfg.display["width"], cfg.display["height"], cfg.display.get("fonts"))
    by_league = {g.league: g for g in games}
    images = [
        r.overview(games[: r.overview_rows], now, 1, 2),
        r.game(by_league["MLB"], now),
        r.game(by_league["NBA"], now),
        r.game(by_league["CPBL"], now),
        r.game(final, now),
        r.game(by_league["F1"], now),
    ]
    tiles = [Renderer.led_preview(img, PITCH) for img in images]
    tw, th = tiles[0].size
    rows = (len(tiles) + COLS - 1) // COLS
    out = Image.new("RGB", (COLS * tw + (COLS - 1) * GAP, rows * th + (rows - 1) * GAP), (40, 40, 40))
    for i, t in enumerate(tiles):
        out.paste(t, ((i % COLS) * (tw + GAP), (i // COLS) * (th + GAP)))
    path = ROOT / "docs" / "preview.png"
    out.save(path, optimize=True)
    print(f"已輸出 {path}（{out.size[0]}x{out.size[1]}）")


if __name__ == "__main__":
    main()
