"""球隊中文名稱與代表色。

顏色會再經過 led_color() 提亮，避免深藍、深綠在 LED 上看不清楚。
找不到的球隊會用 fallback_short_name() 自動縮短，也可以在 config.yaml 的 aliases 覆寫。
"""

from __future__ import annotations

import re


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def led_color(rgb: tuple[int, int, int], min_peak: int = 190) -> tuple[int, int, int]:
    """把顏色等比例放大到最亮通道至少 min_peak，暗色在點陣上才看得到。"""
    peak = max(rgb)
    if peak == 0:
        return (min_peak, min_peak, min_peak)
    if peak >= min_peak:
        return rgb
    k = min_peak / peak
    return tuple(min(255, int(c * k)) for c in rgb)  # type: ignore[return-value]


# MLB：statsapi team id -> (縮寫, 中文, 顏色)
MLB_TEAMS: dict[int, tuple[str, str, str]] = {
    108: ("LAA", "天使", "#BA0021"),
    109: ("ARI", "響尾蛇", "#A71930"),
    110: ("BAL", "金鶯", "#DF4601"),
    111: ("BOS", "紅襪", "#BD3039"),
    112: ("CHC", "小熊", "#3D6BD6"),
    113: ("CIN", "紅人", "#C6011F"),
    114: ("CLE", "守護者", "#E31937"),
    115: ("COL", "洛磯", "#8B74C4"),
    116: ("DET", "老虎", "#FA4616"),
    117: ("HOU", "太空人", "#EB6E1F"),
    118: ("KC", "皇家", "#4F8FDB"),
    119: ("LAD", "道奇", "#1E7FD6"),
    120: ("WSH", "國民", "#D3122B"),
    121: ("NYM", "大都會", "#FF5910"),
    133: ("ATH", "運動家", "#EFB21E"),
    134: ("PIT", "海盜", "#FDB827"),
    135: ("SD", "教士", "#FFC425"),
    136: ("SEA", "水手", "#00B2A9"),
    137: ("SF", "巨人", "#FD5A1E"),
    138: ("STL", "紅雀", "#C41E3A"),
    139: ("TB", "光芒", "#8FBCE6"),
    140: ("TEX", "遊騎兵", "#3A6FD8"),
    141: ("TOR", "藍鳥", "#3B7DD8"),
    142: ("MIN", "雙城", "#D31145"),
    143: ("PHI", "費城人", "#E81828"),
    144: ("ATL", "勇士", "#CE1141"),
    145: ("CWS", "白襪", "#C4CED4"),
    146: ("MIA", "馬林魚", "#00A3E0"),
    147: ("NYY", "洋基", "#5B7FD1"),
    158: ("MIL", "釀酒人", "#FFC52F"),
}

# NBA：標準縮寫 -> (中文, 顏色)
NBA_TEAMS: dict[str, tuple[str, str]] = {
    "ATL": ("老鷹", "#E03A3E"),
    "BOS": ("塞爾提克", "#18A050"),
    "BKN": ("籃網", "#DDDDDD"),
    "CHA": ("黃蜂", "#00A3B4"),
    "CHI": ("公牛", "#CE1141"),
    "CLE": ("騎士", "#C0204F"),
    "DAL": ("獨行俠", "#1E7FD6"),
    "DEN": ("金塊", "#FEC524"),
    "DET": ("活塞", "#C8102E"),
    "GSW": ("勇士", "#FFC72C"),
    "HOU": ("火箭", "#CE1141"),
    "IND": ("溜馬", "#FDBB30"),
    "LAC": ("快艇", "#C8102E"),
    "LAL": ("湖人", "#FDB927"),
    "MEM": ("灰熊", "#6F8DCC"),
    "MIA": ("熱火", "#D0103A"),
    "MIL": ("公鹿", "#3FA34D"),
    "MIN": ("灰狼", "#78BE20"),
    "NOP": ("鵜鶘", "#C8A45A"),
    "NYK": ("尼克", "#F58426"),
    "OKC": ("雷霆", "#007AC1"),
    "ORL": ("魔術", "#0077C0"),
    "PHI": ("七六人", "#1E7FD6"),
    "PHX": ("太陽", "#E56020"),
    "POR": ("拓荒者", "#E03A3E"),
    "SAC": ("國王", "#8A5BC8"),
    "SAS": ("馬刺", "#C4CED4"),
    "TOR": ("暴龍", "#CE1141"),
    "UTA": ("爵士", "#9B6BE0"),
    "WAS": ("巫師", "#E31837"),
}

# ESPN 用的縮寫和 NBA 官方不同，統一轉成官方縮寫
ESPN_NBA_ABBR = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS"}

# 台灣聯盟：用關鍵字比對官方回傳的中文隊名 -> (代碼, 中文短名, 顏色)
CPBL_TEAMS: list[tuple[str, str, str, str]] = [
    ("中信", "CTBC", "中信", "#FFD200"),
    ("兄弟", "CTBC", "中信", "#FFD200"),
    ("味全", "WCD", "味全", "#FF2A2A"),
    ("統一", "UNI", "統一", "#FF8A00"),
    ("樂天", "RKM", "樂天", "#E0305A"),
    ("富邦", "FBG", "富邦", "#3D7BF0"),
    ("台鋼", "TSG", "台鋼", "#20C080"),
]

TPBL_TEAMS: list[tuple[str, str, str, str]] = [
    ("國王", "KINGS", "國王", "#9B6BFF"),
    ("戰神", "MARS", "戰神", "#FF3040"),
    ("攻城獅", "LIONEERS", "攻城獅", "#FF7A1A"),
    ("雲豹", "LEOPARDS", "雲豹", "#1EA8F0"),
    ("夢想家", "DREAMERS", "夢想家", "#FFA21A"),
    ("海神", "AQUAS", "海神", "#20B8E8"),
    ("特攻", "DEA", "特攻", "#FFD200"),
]

# 2026-27 球季四隊。官網賽程頁只寫簡稱（「方舟」「獵鷹」），完整隊名用 PLG_FULL 補上
PLG_TEAMS: list[tuple[str, str, str, str]] = [
    ("勇士", "BRAVES", "勇士", "#3D7BF0"),
    ("領航猿", "PILOTS", "領航猿", "#20C0A0"),
    ("方舟", "ARK", "方舟", "#FF5A3A"),
    ("洋基", "ARK", "方舟", "#FF5A3A"),  # 2026/10/29 前叫「洋基工程」
    ("獵鷹", "GHOSTHAWKS", "獵鷹", "#FFA21A"),
]

PLG_FULL = {
    "BRAVES": "臺北富邦勇士",
    "PILOTS": "桃園璞園領航猿",
    "ARK": "洋基工程方舟",
    "GHOSTHAWKS": "台鋼獵鷹",
}

_CITY_PREFIX = re.compile(r"^(臺北|台北|新北|桃園|新竹|臺中|台中|臺南|台南|高雄|基隆|福爾摩沙)")


def fallback_short_name(full: str, max_chars: int = 3) -> str:
    name = _CITY_PREFIX.sub("", full.strip())
    return name[-max_chars:] if len(name) > max_chars else name


def match_tw_team(table, full: str, aliases: dict[str, str] | None = None):
    """回傳 (code, short, color)。"""
    if aliases and full in aliases:
        short = aliases[full]
    else:
        short = None
    for kw, code, zh, color in table:
        if kw in full:
            return code, short or zh, led_color(hex_rgb(color))
    s = short or fallback_short_name(full)
    return s, s, (230, 230, 230)


def mlb_team(team_id: int, fallback_name: str = ""):
    if team_id in MLB_TEAMS:
        abbr, zh, color = MLB_TEAMS[team_id]
        return abbr, zh, led_color(hex_rgb(color))
    return fallback_name[:3].upper(), fallback_name[:6], (230, 230, 230)


def nba_team(espn_abbr: str, fallback_name: str = ""):
    abbr = ESPN_NBA_ABBR.get(espn_abbr, espn_abbr)
    if abbr in NBA_TEAMS:
        zh, color = NBA_TEAMS[abbr]
        return abbr, zh, led_color(hex_rgb(color))
    return abbr, fallback_name[:6] or abbr, (230, 230, 230)


# ---------- F1 ----------

# 大獎賽名稱關鍵字 → (中文名「X站」, 總覽頁用的 4 字內短名, 正賽總圈數)。比對時由上往下，越特定的放越前面。
# ESPN 只給目前圈數，總圈數照各賽道的正賽圈數填（新賽道不確定就填 None，畫面只顯示目前圈數）
F1_GP = [
    ("Las Vegas", "拉斯維加斯站", "賭城站", 50), ("Miami", "邁阿密站", "邁阿密站", 57), ("United States", "美國站", "美國站", 56),
    ("Mexico", "墨西哥站", "墨西哥站", 71), ("São Paulo", "巴西站", "巴西站", 71), ("Sao Paulo", "巴西站", "巴西站", 71),
    ("Brazil", "巴西站", "巴西站", 71), ("Qatar", "卡達站", "卡達站", 57), ("Abu Dhabi", "阿布達比站", "阿聯酋站", 58),
    ("Australia", "澳洲站", "澳洲站", 58), ("Chin", "中國站", "中國站", 56), ("Japan", "日本站", "日本站", 53),
    ("Bahrain", "巴林站", "巴林站", 57), ("Saudi", "沙烏地站", "沙烏地站", 50), ("Canad", "加拿大站", "加拿大站", 70),
    ("Monaco", "摩納哥站", "摩納哥站", 78), ("Barcelona", "西班牙站", "西班牙站", 66), ("Spanish", "西班牙站", "西班牙站", 66),
    ("Madrid", "馬德里站", "馬德里站", None), ("Austria", "奧地利站", "奧地利站", 71), ("British", "英國站", "英國站", 52),
    ("Belgi", "比利時站", "比利時站", 44), ("Hungar", "匈牙利站", "匈牙利站", 70), ("Dutch", "荷蘭站", "荷蘭站", 72),
    ("Ital", "義大利站", "義大利站", 53), ("Azerbaijan", "亞塞拜然站", "巴庫站", 51), ("Singapore", "新加坡站", "新加坡站", 62),
]

# 賽段代碼 → (中文, 總覽頁短名, config 用的名稱)
F1_SESSIONS = {
    "Race": ("正賽", "正賽", "race"), "Qual": ("排位賽", "排位", "qualifying"),
    "Sprint": ("衝刺賽", "衝刺", "sprint"), "SS": ("衝刺排位", "衝排", "sprint_qualifying"),
    "SQ": ("衝刺排位", "衝排", "sprint_qualifying"),
    "FP1": ("一練", "一練", "practice"), "FP2": ("二練", "二練", "practice"), "FP3": ("三練", "三練", "practice"),
}

# 車隊關鍵字 → (中文短名, 代表色)
F1_TEAMS = [
    ("McLaren", "麥拉倫", "#FF8000"), ("Ferrari", "法拉利", "#E8002D"), ("Red Bull", "紅牛", "#3671C6"),
    ("Mercedes", "賓士", "#27F4D2"), ("Aston", "奧斯頓", "#229971"), ("Alpine", "雅爾派", "#0093CC"),
    ("Williams", "威廉斯", "#64C4FF"), ("Racing Bulls", "小牛", "#6692FF"), ("RB", "小牛", "#6692FF"),
    ("Haas", "哈斯", "#B6BABD"), ("Audi", "奧迪", "#9AA0A6"), ("Sauber", "奧迪", "#9AA0A6"), ("Cadillac", "凱迪拉克", "#D4AF37"),
]

# 資料來源沒給車隊時，用車手代碼對照（2026 車手名單；有人換隊要更新這裡）
F1_DRIVER_TEAM = {
    "VER": "Red Bull", "HAD": "Red Bull", "NOR": "McLaren", "PIA": "McLaren", "LEC": "Ferrari", "HAM": "Ferrari",
    "RUS": "Mercedes", "ANT": "Mercedes", "ALO": "Aston", "STR": "Aston", "GAS": "Alpine", "COL": "Alpine",
    "ALB": "Williams", "SAI": "Williams", "LAW": "Racing Bulls", "LIN": "Racing Bulls", "OCO": "Haas", "BEA": "Haas",
    "HUL": "Audi", "BOR": "Audi", "PER": "Cadillac", "BOT": "Cadillac",
}


def f1_gp(name: str) -> tuple[str, str, int | None]:
    """回傳 (中文名, 短名, 正賽總圈數)。"""
    for kw, full, short, laps in F1_GP:
        if kw.lower() in name.lower():
            return full, short, laps
    base = name.replace("Grand Prix", "").strip()
    return base, base[:4], None


def f1_driver_code(full_name: str) -> str:
    """「Max Verstappen」→ VER；變音符號去掉（Hülkenberg → HUL、Pérez → PER）。"""
    import unicodedata

    last = (full_name.strip().split() or [""])[-1]
    ascii_last = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode()
    return ascii_last[:3].upper()


def f1_team(team_name: str = "", driver_code: str = "") -> tuple[str, tuple[int, int, int]]:
    """回傳 (車隊中文短名, LED 顏色)。"""
    name = team_name or F1_DRIVER_TEAM.get(driver_code, "")
    for kw, zh, color in F1_TEAMS:
        if kw.lower() in name.lower():
            return zh, led_color(hex_rgb(color))
    return "", (230, 230, 230)
