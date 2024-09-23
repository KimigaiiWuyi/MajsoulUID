ACCESS_TOKEN = ""
URL_BASE = "https://game.maj-soul.com/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.88 Safari/537.36"
HEADERS = {
    "User-Agent": USER_AGENT,
    "If-Modified-Since": "0",
    "Referer": URL_BASE,
    "sec-ch-ua": '"Chromium";v="100", "Google Chrome";v="100"',
    "sec-ch-ua-platform": "Windows",
}

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

ModeId2Room = {
    0: "",
    1: "铜之间",
    2: "铜之间 · 四人东",
    3: "铜之间",
    17: "铜之间",
    18: "铜之间",
    4: "银之间",
    5: "银之间 · 四人东",
    6: "银之间",
    19: "银之间",
    20: "银之间",
    7: "金之间",
    8: "金之间 · 四人东",
    9: "金之间 · 四人南",
    21: "金之间 · 三人东",
    22: "金之间 · 三人南",
    10: "玉之间",
    11: "玉之间 · 四人东",
    12: "玉之间 · 四人南",
    23: "玉之间 · 三人东",
    24: "玉之间 · 三人南",
    15: "王座间 · 四人东",
    16: "王座间 · 四人南",
    25: "王座间 · 三人东",
    26: "王座间 · 三人南",
}

PLAYER_RANKS = "初士杰豪圣魂"
LEVEL_KONTEN = 7
LEVEL_MAX_POINT_KONTEN = 2000
