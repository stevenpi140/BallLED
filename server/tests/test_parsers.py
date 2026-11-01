"""解析器測試。樣本是依各來源已知欄位手寫的最小 JSON / HTML，不是真實比賽資料。"""

import json
from datetime import date, datetime, timedelta

from scoreboard.config import Config
from scoreboard.leagues import cpbl, mlb, nba, plg, tpbl

CFG = Config.load(None)
DAY = date(2026, 10, 26)


def test_mlb_live_and_filters_by_taipei_date():
    data = {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": 1,
                        "gameDate": "2026-10-26T00:08:00Z",  # 台灣 10/26 08:08
                        "gameType": "W",
                        "seriesGameNumber": 3,
                        "status": {"abstractGameState": "Live", "codedGameState": "I", "detailedState": "In Progress"},
                        "teams": {
                            "away": {"team": {"id": 119, "name": "Los Angeles Dodgers"}, "score": 3},
                            "home": {"team": {"id": 141, "name": "Toronto Blue Jays"}, "score": 2},
                        },
                        "linescore": {
                            "currentInning": 7, "isTopInning": False, "inningState": "Bottom", "outs": 1,
                            "teams": {"away": {"runs": 3, "hits": 7, "errors": 0}, "home": {"runs": 2, "hits": 5, "errors": 1}},
                            "offense": {"first": {"id": 1}, "third": {"id": 2}},
                        },
                        "venue": {"name": "Rogers Centre"},
                    },
                    {
                        "gamePk": 2,
                        "gameDate": "2026-10-25T00:08:00Z",  # 台灣 10/25，應被排除
                        "status": {"abstractGameState": "Final"},
                        "teams": {"away": {"team": {"id": 147}}, "home": {"team": {"id": 111}}},
                    },
                ]
            }
        ]
    }
    games = mlb.parse(data, CFG, DAY)
    assert len(games) == 1
    g = games[0]
    assert (g.away.name, g.home.name) == ("道奇", "藍鳥")
    assert (g.away.score, g.home.score) == (3, 2)
    assert g.state == "live" and g.status_text() == "7局下"
    assert g.bases == (True, False, True) and g.outs == 1
    assert g.label == "世界大賽G3"
    assert (g.away.hits, g.away.errors, g.home.hits, g.home.errors) == (7, 0, 5, 1)


def test_mlb_end_of_inning_shows_next_half():
    data = {"dates": [{"games": [{
        "gamePk": 3, "gameDate": "2026-10-26T00:00:00Z",
        "status": {"abstractGameState": "Live"},
        "teams": {"away": {"team": {"id": 119}, "score": 0}, "home": {"team": {"id": 141}, "score": 0}},
        "linescore": {"currentInning": 4, "isTopInning": False, "inningState": "End"},
    }]}]}
    g = mlb.parse(data, CFG, DAY)[0]
    assert g.status_text() == "5局上"


def test_nba_espn():
    data = {
        "events": [
            {
                "id": "401",
                "date": "2026-10-26T02:30Z",
                "competitions": [
                    {
                        "status": {"period": 3, "displayClock": "5:21", "type": {"state": "in", "name": "STATUS_IN_PROGRESS"}},
                        "competitors": [
                            {"homeAway": "home", "score": "91", "team": {"abbreviation": "LAL"}},
                            {"homeAway": "away", "score": "88", "team": {"abbreviation": "GS"}},
                        ],
                    }
                ],
            }
        ]
    }
    g = nba.parse(data, CFG, DAY)[0]
    assert (g.away.code, g.away.name, g.away.score) == ("GSW", "勇士", 88)
    assert g.status_text() == "Q3 5:21"


def test_cpbl_rows():
    rows = [
        {
            "GameSno": 6, "GameDate": "2026-10-26T00:00:00", "PreExeDate": "2026-10-26T18:35:00",
            "VisitingTeamName": "中信兄弟", "HomeTeamName": "味全龍",
            "VisitingFirstMover": "勝騎士", "HomeFirstMover": "梅賽德",
            "FieldAbbe": "大巨蛋", "GameStatus": 1,
        },
        {"GameSno": 5, "GameDate": "2026-10-25T00:00:00", "PreExeDate": "2026-10-25T18:35:00",
         "VisitingTeamName": "味全龍", "HomeTeamName": "中信兄弟", "GameStatus": 3},
    ]
    games = cpbl.parse_rows(rows, "C", CFG, DAY, now=datetime(2026, 10, 26, 12, tzinfo=CFG.tz))
    assert len(games) == 1
    g = games[0]
    assert g.state == "pre" and g.label == "台灣大賽G6"
    assert (g.away.name, g.home.name) == ("中信", "味全")
    assert g.away.pitcher == "勝騎士"
    assert g.start.hour == 18 and g.start.minute == 35


def test_cpbl_gamedatas_string_and_token():
    assert cpbl.decode_list({"Success": True, "GameDatas": '[{"GameSno": 1}]'}) == [{"GameSno": 1}]
    html = "headers: { RequestVerificationToken: 'abcDEF1234567890abcDEF1234567890' }"
    assert cpbl.find_token(html) == "abcDEF1234567890abcDEF1234567890"


def test_cpbl_live_scoreboard():
    g = cpbl.parse_rows([{"GameSno": 1, "PreExeDate": "2026-10-26T18:35:00", "VisitingTeamName": "樂天桃猿",
                          "HomeTeamName": "統一7-ELEVEn獅", "GameStatus": 2, "VisitingScore": 1, "HomeScore": 0}],
                        "A", CFG, DAY)[0]
    cpbl.apply_live(g, {"ScoreboardJson": '[{"InningSeq":1,"VisitingHomeType":1,"ScoreCnt":1},'
                                          '{"InningSeq":1,"VisitingHomeType":2,"ScoreCnt":0},'
                                          '{"InningSeq":2,"VisitingHomeType":1,"ScoreCnt":2}]'})
    assert g.status_text() == "2局上"
    assert (g.away.score, g.home.score) == (3, 0)


def test_cpbl_schedule_finished_game_not_pre():
    # 10/17 台灣大賽 G1：賽程介面的 PresentStatus=1，但其實已經比完 0:3（之前被誤判成未開打）
    row = {"GameSno": 1, "KindCode": "C", "PresentStatus": 1, "GameDate": "2026-10-17T00:00:00",
           "PreExeDate": "2026-10-17T18:35:00", "GameDateTimeS": "2026-10-17T18:35:00",
           "GameDateTimeE": "2026-10-17T21:41:00", "GameResult": "2", "IsGameStop": "N",
           "VisitingTeamName": "中信兄弟", "HomeTeamName": "味全龍", "VisitingScore": 0, "HomeScore": 3,
           "VisitingPitcherName": "勝騎士", "HomePitcherName": "梅賽德"}
    later = datetime(2026, 10, 27, 12, tzinfo=CFG.tz)
    g = cpbl.parse_rows([row], "C", CFG, date(2026, 10, 17), now=later)[0]
    assert g.state == "final" and (g.away.score, g.home.score) == (0, 3)
    # 就算只有比分、其他欄位都空的，過了那一天也一定是終場
    bare = {k: row[k] for k in ("GameSno", "GameDate", "PreExeDate", "VisitingTeamName", "HomeTeamName",
                                "VisitingScore", "HomeScore", "PresentStatus")}
    assert cpbl.parse_rows([bare], "C", CFG, date(2026, 10, 17), now=later)[0].state == "final"
    # 還沒開打的比賽（比分 0:0、沒有開打／結束時間）
    future = {**bare, "VisitingScore": 0, "HomeScore": 0}
    before = datetime(2026, 10, 17, 12, tzinfo=CFG.tz)
    assert cpbl.parse_rows([future], "C", CFG, date(2026, 10, 17), now=before)[0].state == "pre"


def _box(status, chi, board, logs=None):
    return {"Success": True,
            "GameDetailJson": json.dumps([{"GameStatus": status, "GameStatusChi": chi}]),
            "ScoreboardJson": json.dumps(board), "LiveLogJson": json.dumps(logs or [])}


def test_cpbl_getlive_final_with_rhe():
    g = cpbl.parse_rows([{"GameSno": 7, "PreExeDate": "2026-10-26T18:35:00", "VisitingTeamName": "中信兄弟",
                          "HomeTeamName": "味全龍"}], "C", CFG, DAY, now=datetime(2026, 10, 26, 18, tzinfo=CFG.tz))[0]
    board = [{"InningSeq": i, "VisitingHomeType": vh, "ScoreCnt": r, "HittingCnt": h, "ErrorCnt": e}
             for i in range(1, 11) for vh, r, h, e in ((1, 0, 1, 0), (2, 1 if i == 10 else 0, 1, 1 if i == 3 else 0))]
    cpbl.apply_live(g, _box(3, "比賽結束", board))
    assert g.state == "final" and g.status_text() == "終場 10局"
    assert (g.away.score, g.away.hits, g.away.errors) == (0, 10, 0)
    assert (g.home.score, g.home.hits, g.home.errors) == (1, 10, 1)


def test_cpbl_getlive_live_count_from_livelog():
    g = cpbl.parse_rows([{"GameSno": 7, "PreExeDate": "2026-10-26T18:35:00", "VisitingTeamName": "中信兄弟",
                          "HomeTeamName": "味全龍"}], "C", CFG, DAY, now=datetime(2026, 10, 26, 18, tzinfo=CFG.tz))[0]
    board = [{"InningSeq": 1, "VisitingHomeType": 1, "ScoreCnt": 2, "HittingCnt": 3, "ErrorCnt": 0},
             {"InningSeq": 1, "VisitingHomeType": 2, "ScoreCnt": 0, "HittingCnt": 0, "ErrorCnt": 1},
             {"InningSeq": 2, "VisitingHomeType": 1, "ScoreCnt": 0, "HittingCnt": 1, "ErrorCnt": 0}]
    logs = [  # 故意打亂順序：要照局數與事件編號排序，不能直接取最後一筆
        {"InningSeq": 2, "VisitingHomeType": "1", "MainEventNo": "0031", "OutCnt": 1, "BallCnt": 2, "StrikeCnt": 1,
         "FirstBase": "0000001234", "SecondBase": "", "ThirdBase": "0000005678", "PitcherName": "梅賽德",
         "HitterName": "許基宏"},
        {"InningSeq": 1, "VisitingHomeType": "2", "MainEventNo": "0020", "OutCnt": 3},
    ]
    cpbl.apply_live(g, _box(2, "比賽中", board, logs))
    assert g.state == "live" and g.status_text() == "2局上"
    assert (g.away.score, g.away.hits, g.home.errors) == (2, 4, 1)
    assert g.outs == 1 and g.bases == (True, False, True)
    assert g.extra["balls"] == 2 and g.extra["batter"] == "許基宏"
    # 最後一筆是三出局：直接顯示下一個半局
    cpbl.apply_live(g, _box(2, "比賽中", board, [{"InningSeq": 2, "VisitingHomeType": "1", "MainEventNo": "40",
                                                 "OutCnt": 3, "FirstBase": "x"}]))
    assert g.status_text() == "2局下" and g.outs == 0 and g.bases == (False, False, False)


def test_cpbl_falls_back_to_rebas(monkeypatch):
    import asyncio

    c = cpbl.CPBL()

    async def official(*a):
        raise RuntimeError("HTTP 404")

    async def rebas(*a):
        return ["rebas-game"]

    monkeypatch.setattr(c, "fetch_official", official)
    monkeypatch.setattr(c.rebas, "fetch", rebas)
    assert asyncio.run(c.fetch(None, CFG, DAY)) == ["rebas-game"]


def test_tpbl():
    rows = [{
        "id": 77, "game_date": "2026-10-26", "game_time": "17:00:00", "status": "COMPLETED",
        "home_team": {"name": "新北國王", "won_score": 101}, "away_team": {"name": "新竹御嵿攻城獅", "won_score": 99},
    }]
    g = tpbl.parse(rows, CFG, DAY)[0]
    assert g.state == "final" and (g.away.name, g.home.name) == ("攻城獅", "國王")
    assert g.home.score == 101


def test_plg_schedule_html():
    html = """
    <div class="match_row d-2026-10 is-future">
      <div class="match_row_datetime"><h5>10/26</h5><h6>(一) 19:00</h6></div>
      <div class="text-black"><span class="PC_only">桃園璞園領航猿</span></div>
      <div class="text-black"><span class="PC_only">臺北富邦勇士</span></div>
      <h6 class="PC_only ff8bit">0</h6><h6 class="PC_only ff8bit">0</h6>
      <a href="/game/123">數據</a>
    </div>"""
    rows = plg.parse_schedule(html)
    assert rows[0]["id"] == "123" and rows[0]["date"] == DAY and rows[0]["time"] == (19, 0)
    before = datetime(2026, 10, 26, 12, tzinfo=CFG.tz)
    g = plg.to_game(rows[0], CFG, before)
    assert g.state == "pre" and (g.away.name, g.home.name) == ("領航猿", "勇士")
    during = before.replace(hour=19) + timedelta(minutes=30)
    assert plg.to_game(rows[0], CFG, during).state == "live"


def test_follow_filter():
    cfg = Config({**CFG.raw, "leagues": {**CFG.raw["leagues"], "mlb": {"enabled": True, "teams": ["LAD"]},
                                         "cpbl": {"enabled": True, "teams": ["中信"]}}})
    assert cfg.follows("mlb", "LAD")
    assert not cfg.follows("mlb", "NYY", "洋基")
    assert cfg.follows("cpbl", "CTBC", "中信", "中信兄弟")
    assert cfg.follows("nba", "BOS")  # 空清單 = 全部


def test_rebas_cpbl():
    from scoreboard.leagues import rebas

    rows = [
        {
            "uniqid": "A1", "group": "例行賽",
            "away": {"team": "富邦悍將", "runs": 6, "hits": 9, "errors": 0, "scores": ["5", "0", "1"], "scheduled_SP": None},
            "home": {"team": "樂天桃猿", "runs": 1, "hits": 3, "errors": 2, "scores": ["0", "0"], "scheduled_SP": None},
            "info": {"status": "PLAYING", "finished_status": "NORMAL", "scheduled_start_at": "2026-10-26 18:35",
                     "started_at": "2026-10-26 18:35", "ended_at": "", "location_abbr": "樂天桃園", "innings": 3},
        },
        {
            "uniqid": "A2", "group": "季後賽", "seq": 7,
            "season": {"title": "中職2026年-台灣大賽", "type": "POSTSEASON", "uniqid": "CPBL-2026-LU"},
            "away": {"team": "中信兄弟", "runs": 0, "scores": [], "scheduled_SP": "勝騎士"},
            "home": {"team": "味全龍", "runs": 0, "scores": [], "scheduled_SP": "梅賽德"},
            "info": {"status": "NOT_STARTED", "finished_status": "NORMAL", "scheduled_start_at": "2026-10-26 18:35",
                     "started_at": "", "ended_at": ""},
        },
        {"uniqid": "A3", "away": {}, "home": {}, "info": {"scheduled_start_at": "2026-10-25 18:35"}},
    ]
    games = rebas.parse(rows, CFG, DAY)
    assert len(games) == 2
    live, pre = games
    assert live.state == "live" and live.status_text() == "3局上"
    assert (live.away.name, live.away.score, live.home.name, live.home.score) == ("富邦", 6, "樂天", 1)
    assert live.venue == "樂天桃園" and live.label == ""
    assert (live.away.hits, live.home.errors) == (9, 2)
    assert pre.away.hits is None
    assert pre.state == "pre" and pre.away.score is None
    assert pre.away.pitcher == "勝騎士" and pre.label == "台灣大賽G7"


def test_tpbl_completed_without_status_update():
    rows = [{
        "id": 1, "game_date": "2026-10-24", "game_time": "14:30:00", "status": "SCHEDULED", "is_live": False,
        "home_team": {"name": "新竹御嵿攻城獅", "won_score": 100}, "away_team": {"name": "福爾摩沙夢想家", "won_score": 90},
    }]
    g = tpbl.parse(rows, CFG, date(2026, 10, 24))[0]
    assert g.state == "final" and g.home.score == 100


def test_rebas_picks_seasons_covering_day():
    from scoreboard.leagues import rebas

    seasons = [
        {"uniqid": "CPBL-2026-LU", "type": "POSTSEASON", "start_at": "2026-10-17T00:00:00+08:00",
         "end_at": "2026-10-28T00:00:00+08:00"},
        {"uniqid": "CPBL-2026-YQ", "type": "CHALLENGE", "start_at": "2026-10-09T00:00:00+08:00",
         "end_at": "2026-10-13T00:00:00+08:00"},
        {"uniqid": "CPBL-2026-oB", "type": "REGULAR", "start_at": "2026-03-28T00:00:00+08:00",
         "end_at": "2026-10-07T00:00:00+08:00"},
    ]
    assert rebas.seasons_for(seasons, DAY) == ["CPBL-2026-LU"]
    assert rebas.seasons_for(seasons, date(2026, 10, 8)) == ["CPBL-2026-YQ", "CPBL-2026-oB"]
    assert rebas.seasons_for(seasons, date(2026, 12, 1)) == []  # 休賽季：沒有比賽


def test_tpbl_postponed_still_not_started():
    # 2026-10-24 薔琵颱風延賽的兩場，API 一直維持 NOT_STARTED、沒有比分
    rows = [{
        "id": 27544, "game_date": "2026-10-24", "game_time": "14:30:00", "status": "NOT_STARTED", "is_live": False,
        "home_team": {"name": "新竹御嵿攻城獅", "won_score": None}, "away_team": {"name": "福爾摩沙夢想家", "won_score": None},
    }]
    g = tpbl.parse(rows, CFG, date(2026, 10, 24))[0]
    assert g.state == "postponed" and g.status_text() == "延賽"
    assert g.home.score is None


def test_live_poll_interval_per_league():
    from scoreboard.aggregator import Aggregator
    from scoreboard.demo import demo_games
    from scoreboard.leagues.cpbl import CPBL
    from scoreboard.leagues.mlb import MLB

    agg = Aggregator(CFG)
    live = [g for g in demo_games(CFG) if g.state == "live"]
    assert agg._interval(MLB(), live) == 2
    assert agg._interval(CPBL(), live) == 5  # 官網單場資料含整場逐球紀錄，最快 5 秒


def _f1_comp(cid, abbr, start, state, period, order=None):
    names = ["Max Verstappen", "Lando Norris", "Charles Leclerc", "Nico Hülkenberg"]
    comps = []
    for i, n in enumerate(names):
        x = {"id": str(i), "type": "athlete", "winner": bool(order and i == 0), "athlete": {"fullName": n}}
        if order:
            x["order"] = order[i]
        comps.append(x)
    return {"id": cid, "type": {"abbreviation": abbr}, "date": start, "startDate": start,
            "status": {"period": period, "type": {"state": state, "name": "STATUS_X", "description": state}},
            "competitors": comps}


def test_f1_espn_sessions():
    from scoreboard.leagues import f1

    data = {"events": [{"name": "Mexico City Grand Prix", "competitions": [
        _f1_comp("1", "FP3", "2026-10-31T17:30Z", "post", 26, [1, 2, 3, 4]),          # 練習賽預設不顯示
        _f1_comp("2", "Qual", "2026-10-31T21:00Z", "post", 20, [3, 1, 2, 4]),          # 台灣 11/1 05:00
        _f1_comp("3", "Race", "2026-11-01T20:00Z", "in", 34, [2, 1, 4, 3]),            # 台灣 11/2 04:00
    ]}]}
    q = f1.parse(data, CFG, date(2026, 11, 1))
    assert len(q) == 1 and q[0].race.session == "排位賽" and q[0].state == "final"
    assert [d.code for d in q[0].race.drivers] == ["NOR", "LEC", "VER", "HUL"]
    race = f1.parse(data, CFG, date(2026, 11, 2))[0]
    assert race.state == "live" and race.race.lap == 34 and race.race.laps == 71
    assert race.status_text() == "第 34/71 圈" and race.short_status() == "L34"
    lead = race.race.drivers[0]
    assert (lead.code, lead.team) == ("NOR", "麥拉倫")
    assert (race.away.name, race.home.name) == ("墨西哥站", "正賽")
    assert race.race.gp == "墨西哥站"


def test_f1_pre_race_has_no_order():
    from scoreboard.leagues import f1

    data = {"events": [{"name": "Las Vegas Grand Prix", "competitions": [
        _f1_comp("9", "Race", "2026-11-21T04:00Z", "pre", 0)]}]}
    g = f1.parse(data, CFG, date(2026, 11, 21))[0]
    assert g.state == "pre" and g.race.drivers == [] and g.race.gp == "拉斯維加斯站" and g.away.name == "賭城站"
    assert g.status_text() == "12:00 開始"


def test_f1_live_merge_and_board():
    from scoreboard.leagues.f1_live import LiveTiming

    lt = LiveTiming()
    lt._apply("DriverList", {"1": {"Tla": "NOR", "TeamName": "McLaren", "TeamColour": "FF8000"},
                             "16": {"Tla": "LEC", "TeamName": "Ferrari", "TeamColour": "E8002D"}})
    lt._apply("TimingData", {"Lines": {"1": {"Position": "1", "GapToLeader": "LAP 34"},
                                       "16": {"Position": "2", "GapToLeader": "+1.234", "Sectors": [{"Value": "20.1"}]}}})
    # 差異更新只帶有變的欄位：名次互換，其他欄位要保留
    lt._apply("TimingData", {"Lines": {"1": {"Position": "2", "GapToLeader": "+0.4"},
                                       "16": {"Position": "1", "GapToLeader": "LAP 35", "Sectors": {"0": {"Value": "19.9"}}}}})
    lt._apply("LapCount", {"CurrentLap": 35, "TotalLaps": 71})
    lt._apply("SessionInfo", {"Meeting": {"Name": "Mexico City Grand Prix"}, "Name": "Race", "Type": "Race"})
    b = lt.board()
    assert [d["tla"] for d in b["drivers"]] == ["LEC", "NOR"]
    assert b["drivers"][1]["gap"] == "+0.4" and b["lap"] == 35 and b["laps"] == 71
    assert lt.state["TimingData"]["Lines"]["16"]["Sectors"]["0"]["Value"] == "19.9"
    assert lt.matches("race") and not lt.matches("qualifying") and not lt.matches("sprint")


def test_f1_live_token_expiry():
    import base64
    import json as _json

    from scoreboard.leagues.f1_live import LiveTiming, token_expiry

    payload = base64.urlsafe_b64encode(_json.dumps({"exp": 1793600000}).encode()).decode().rstrip("=")
    tok = f"xx.{payload}.yy"
    assert token_expiry(tok) == 1793600000
    assert token_expiry("not-a-jwt") is None
    lt = LiveTiming(tok)
    assert lt._auth() == {"Authorization": f"Bearer {tok}"} and LiveTiming()._auth() == {}


def test_f1_enrich_from_livetiming():
    from scoreboard.leagues import f1

    data = {"events": [{"name": "Mexico City Grand Prix", "competitions": [
        _f1_comp("3", "Race", "2026-11-01T20:00Z", "in", 34, [2, 1, 4, 3])]}]}
    g = f1.parse(data, CFG, date(2026, 11, 2))[0]
    board = {"lap": 35, "laps": 71, "drivers": [
        {"pos": 1, "tla": "NOR", "team": "McLaren", "colour": "FF8000", "gap": "LAP 35"},
        {"pos": 2, "tla": "VER", "team": "Red Bull Racing", "colour": "3671C6", "gap": "+1.234"},
        {"pos": 3, "tla": "LEC", "team": "Ferrari", "colour": "E8002D", "gap": "+4.9"}]}
    f1.enrich(g, board)
    assert [(d.code, d.gap) for d in g.race.drivers] == [("NOR", ""), ("VER", "+1.234"), ("LEC", "+4.9")]
    assert g.race.drivers[1].team == "紅牛" and g.race.lap == 35 and g.status_text() == "第 35/71 圈"


def test_f1_token_extract_and_write(tmp_path):
    import json as _json
    from urllib.parse import quote

    from scoreboard.leagues.f1_live import extract_token, set_config_token

    tok = "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3OTM2MDAwMDB9.c2lnbmF0dXJl"
    cookie = quote(_json.dumps({"data": {"subscriptionStatus": "active", "subscriptionToken": tok}}))
    assert extract_token(cookie) == tok
    assert extract_token(quote(cookie)) == tok          # 編碼兩次也找得到
    assert extract_token(f'  "{tok}" ') == tok          # 直接貼 token 也可以
    assert extract_token("hello") is None

    cfg = tmp_path / "config.yaml"
    cfg.write_text('leagues:\n  f1:\n    enabled: true  # 註解保留\n    live_token: ""\n', encoding="utf-8")
    assert set_config_token(cfg, tok)
    text = cfg.read_text(encoding="utf-8")
    assert f'live_token: "{tok}"' in text and "# 註解保留" in text
    cfg2 = tmp_path / "c2.yaml"
    cfg2.write_text("leagues:\n  f1:\n    enabled: true\n", encoding="utf-8")
    assert set_config_token(cfg2, tok) and f'    live_token: "{tok}"' in cfg2.read_text(encoding="utf-8")
    assert not set_config_token(tmp_path / "none.yaml", tok)
