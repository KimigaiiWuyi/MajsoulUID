# words that can end up in log, some are mandatory kanji in places
JPNAME = 0
RONAME = 1
ENNAME = 2

# senkinin barai yaku - please don't change, yostar..
DAISANGEN = 37  # daisangen cfg.fan.fan.map_ index
DAISUUSHI = 50

TSUMOGIRI = 60  # tenhou tsumogiri symbol

RUNES = {
    # wind
    "east": ["東", "East ", "East "],
    "south": ["南", "South ", "South "],
    "west": ["西", "West ", "West "],
    "north": ["北", "North ", "North "],
    # yaku
    "bakaze": ["場風", "? ", "? "],
    "jikaze": ["自風", "? ", "? "],
    "dabururiichi": ["両立直", "Double Riichi ", "Double Riichi "],
    # hand limits
    "mangan": ["満貫", "Mangan ", "Mangan "],
    "haneman": ["跳満", "Haneman ", "Haneman "],
    "baiman": ["倍満", "Baiman ", "Baiman "],
    "sanbaiman": ["三倍満", "Sanbaiman ", "Sanbaiman "],
    "yakuman": ["役満", "Yakuman ", "Yakuman "],
    "kazoeyakuman": ["数え役満", "Kazoe Yakuman ", "Counted Yakuman "],
    "kiriagemangan": ["切り上げ満貫", "Kiriage Mangan ", "Rounded Mangan "],
    # round enders
    "agari": ["和了", "Agari", "Agari"],
    "ryuukyoku": ["流局", "Ryuukyoku", "Exhaustive Draw"],
    "nagashimangan": ["流し満貫", "Nagashi Mangan", "Mangan at Draw"],
    "suukaikan": ["四開槓", "Suukaikan", "Four Kan Abortion"],
    "sanchahou": ["三家和", "Sanchahou", "Three Ron Abortion"],
    "kyuushukyuuhai": [
        "九種九牌",
        "Kyuushu Kyuuhai",
        "Nine Terminal Abortion",
    ],
    "suufonrenda": ["四風連打", "Suufon Renda", "Four Wind Abortion"],
    "suuchariichi": ["四家立直", "Suucha Riichi", "Four Riichi Abortion"],
    # scoring
    "fu": ["符", "符", "Fu"],
    "han": ["飜", "飜", "Han"],
    "points": ["点", "点", "Points"],
    "all": ["∀", "∀", "∀"],
    "pao": ["包", "pao", "Responsibility"],
    # rooms
    "tonpuu": ["東喰", " East", " East"],
    "hanchan": ["南喰", " South", " South"],
    "friendly": ["友人戦", "Friendly", "Friendly"],
    "tournament": ["大会戦", "Tounament", "Tournament"],
    "sanma": ["三", "3-Player ", "3-Player "],
    "red": ["赤", " Red", " Red Fives"],
    "nored": ["", " Aka Nashi", " No Red Fives"],
}

YSCORE = [
    # oya,    ko,   ron  pays
    [0, 16000, 48000],  # oya wins
    [16000, 8000, 32000],  # ko  wins
]
