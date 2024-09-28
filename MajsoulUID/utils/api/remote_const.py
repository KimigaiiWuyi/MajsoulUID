from enum import Enum

from .models import Stats, Extended

# 定义一个常量，表示玩家的等级
PLAYER_RANKS = "初士杰豪圣魂"
PLAYER_RANKS_DETAIL = ["初心", "雀士", "雀杰", "雀豪", "雀圣", "魂天"]
LEVEL_KONTEN = 7
LEVEL_MAX_POINT_KONTEN = 2000

LEVEL_MAX_POINTS = [
    20,
    80,
    200,
    600,
    800,
    1000,
    1200,
    1400,
    2000,
    2800,
    3200,
    3600,
    4000,
    6000,
    9000,
]
LEVEL_PENALTY = [
    0,
    0,
    0,
    20,
    40,
    60,
    80,
    100,
    120,
    165,
    180,
    195,
    210,
    225,
    240,
    255,
]
LEVEL_PENALTY_3 = [
    0,
    0,
    0,
    20,
    40,
    60,
    80,
    100,
    120,
    165,
    190,
    215,
    240,
    265,
    290,
    320,
]
LEVEL_PENALTY_E = [
    0,
    0,
    0,
    10,
    20,
    30,
    40,
    50,
    60,
    80,
    90,
    100,
    110,
    120,
    130,
    140,
]
LEVEL_PENALTY_E_3 = [
    0,
    0,
    0,
    10,
    20,
    30,
    40,
    50,
    60,
    80,
    95,
    110,
    125,
    140,
    160,
    175,
]


class GameMode(Enum):
    王座 = 16
    玉 = 12
    金 = 9
    王东 = 15
    玉东 = 11
    金东 = 8
    三金 = 22
    三玉 = 24
    三王座 = 26
    三金东 = 21
    三玉东 = 23
    三王东 = 25


MODE_PENALTY = {
    GameMode.金: LEVEL_PENALTY,
    GameMode.玉: LEVEL_PENALTY,
    GameMode.王座: LEVEL_PENALTY,
    GameMode.金东: LEVEL_PENALTY_E,
    GameMode.玉东: LEVEL_PENALTY_E,
    GameMode.王东: LEVEL_PENALTY_E,
    GameMode.三金: LEVEL_PENALTY_3,
    GameMode.三玉: LEVEL_PENALTY_3,
    GameMode.三王座: LEVEL_PENALTY_3,
    GameMode.三金东: LEVEL_PENALTY_E_3,
    GameMode.三玉东: LEVEL_PENALTY_E_3,
    GameMode.三王东: LEVEL_PENALTY_E_3,
}

LEVEL_ALLOWED_MODES = {
    101: [],
    102: [],
    103: [GameMode.金, GameMode.金东],
    104: [GameMode.金, GameMode.玉, GameMode.金东, GameMode.玉东],
    105: [GameMode.玉, GameMode.王座, GameMode.玉东, GameMode.王东],
    106: [GameMode.王座, GameMode.王东],
    107: [GameMode.王座, GameMode.王东],
    201: [],
    202: [],
    203: [GameMode.三金, GameMode.三金东],
    204: [GameMode.三金, GameMode.三玉, GameMode.三金东, GameMode.三玉东],
    205: [GameMode.三玉, GameMode.三王座, GameMode.三玉东, GameMode.三王东],
    206: [GameMode.三王座, GameMode.三王东],
    207: [GameMode.三王座, GameMode.三王东],
}

player_stats_zero: Stats = {
    "count": 0,
    "level": {"id": 10101, "score": 0, "delta": 0},
    "max_level": {"id": 10101, "score": 0, "delta": 0},
    "rank_rates": [
        0,
        0,
        0,
        0,
    ],
    "rank_avg_score": [0, 0, 0, 0],
    "avg_rank": 4,
    "negative_rate": 0,
    "id": 0,
    "nickname": "Player",
    "played_modes": [12, 11, 8, 9],
}

player_extend_zero: Extended = {
    "count": 0,
    "和牌率": 0,
    "自摸率": 0,
    "默听率": 0,
    "放铳率": 0,
    "副露率": 0,
    "立直率": 0,
    "平均打点": 0,
    "最大连庄": 0,
    "和了巡数": 0,
    "平均铳点": 0,
    "流局率": 0,
    "流听率": 0,
    "一发率": 0,
    "里宝率": 0,
    "被炸率": 0,
    "平均被炸点数": 0,
    "放铳时立直率": 0,
    "放铳时副露率": 0,
    "立直后放铳率": 0,
    "立直后非瞬间放铳率": 0,
    "副露后放铳率": 0,
    "立直后和牌率": 0,
    "副露后和牌率": 0,
    "立直后流局率": 0,
    "副露后流局率": 0,
    "放铳至立直": 0,
    "放铳至副露": 0,
    "放铳至默听": 0,
    "立直和了": 0,
    "副露和了": 0,
    "默听和了": 0,
    "立直巡目": 0,
    "立直收支": 0,
    "立直收入": 0,
    "立直支出": 0,
    "先制率": 0,
    "追立率": 0,
    "被追率": 0,
    "振听立直率": 0,
    "立直好型": 0,
    "立直多面": 0,
    "立直好型2": 0,
    "最大累计番数": 0,
    "W立直": 0,
    "打点效率": 0,
    "铳点损失": 0,
    "净打点效率": 0,
    "平均起手向听": 0,
    "平均起手向听亲": 0,
    "平均起手向听子": 0,
    "最近大铳": {
        "id": "",
        "start_time": 0,
        "fans": [],
    },
    "id": 0,
    "played_modes": [9, 11, 8, 12],
}
