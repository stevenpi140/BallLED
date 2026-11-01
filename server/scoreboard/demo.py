"""假資料：只用來在沒有網路或沒有比賽時預覽版面，不是真實比分。"""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import Config
from .models import Driver, Game, Race, TeamLine
from .teams import CPBL_TEAMS, TPBL_TEAMS, PLG_TEAMS, f1_team, match_tw_team, mlb_team, nba_team


def _tw(table, full, score=None, pitcher=None):
    code, short, color = match_tw_team(table, full)
    return TeamLine(code, short, full, color, score, pitcher)


def _mlb(tid, score, hits=None, errors=None):
    code, zh, color = mlb_team(tid)
    return TeamLine(code, zh, code, color, score, hits=hits, errors=errors)


def _nba(abbr, score):
    code, zh, color = nba_team(abbr)
    return TeamLine(code, zh, code, color, score)


def _f1(pos, code):
    team, color = f1_team("", code)
    return Driver(pos, code, team, color)


def demo_games(cfg: Config) -> list[Game]:
    now = datetime.now(cfg.tz).replace(second=0, microsecond=0)
    return [
        Game("MLB", "d1", now - timedelta(hours=2), "live", _mlb(119, 3, 7, 0), _mlb(141, 2, 11, 1),
             venue="Rogers Centre", label="世界大賽G3", inning=7, top=False, outs=1, bases=(True, False, True)),
        Game("NBA", "d2", now - timedelta(hours=1), "live", _nba("GS", 88), _nba("LAL", 91),
             label="", period=3, clock="05:21"),
        Game("CPBL", "d3", now + timedelta(minutes=17), "pre",
             _tw(CPBL_TEAMS, "中信兄弟", pitcher="勝騎士"), _tw(CPBL_TEAMS, "味全龍", pitcher="梅賽德"),
             venue="大巨蛋", label="台灣大賽G7"),
        Game("TPBL", "d4", now + timedelta(hours=3), "pre",
             _tw(TPBL_TEAMS, "新北國王"), _tw(TPBL_TEAMS, "新竹御嵿攻城獅"), venue="新莊體育館"),
        Game("PLG", "d5", now - timedelta(hours=4), "final",
             _tw(PLG_TEAMS, "臺北富邦勇士", 102), _tw(PLG_TEAMS, "桃園璞園領航猿", 95)),
        Game("F1", "d6", now - timedelta(minutes=50), "live",
             TeamLine("F1", "墨西哥", "墨西哥", (235, 235, 240)), TeamLine("race", "正賽", "正賽", (255, 210, 60)),
             race=Race("墨西哥", "正賽", lap=34, laps=71, drivers=[_f1(1, "VER"), _f1(2, "NOR"), _f1(3, "LEC")])),
    ]
