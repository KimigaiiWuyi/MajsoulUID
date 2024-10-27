from pathlib import Path
from copy import deepcopy
from typing import Optional

from PIL import Image, ImageDraw
from gsuid_core.models import Event
from gsuid_core.utils.cache import gs_cache
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.fonts.fonts import core_font as majs_font

from ..utils.majs_api import majs_api
from ..utils.api.remote import PlayerLevel
from ..utils.image import get_bg, get_footer
from ..utils.api.models import Stats, Extended
from ..majs_config.majs_config import MAJS_CONFIG
from ..utils.api.remote_const import player_stats_zero, player_extend_zero

TEXTURE = Path(__file__).parent / "texture2d"
star_empty = Image.open(TEXTURE / "star_empty.png").resize((32, 32))
star_full = Image.open(TEXTURE / "star_full.png").resize((32, 32))

RANK_ALPHA = {2: 0.7, 3: 0.5, 4: 0.1}
RANK_POS = {4: 321, 3: 242, 2: 160, 1: 73}
W = (255, 255, 255)
G = (193, 193, 193)


async def draw_title(msg: str):
    title = Image.open(TEXTURE / "title.png")
    title_draw = ImageDraw.Draw(title)

    title_draw.text(
        (504, 435),
        msg,
        W,
        majs_font(30),
        "mm",
    )

    return title


@gs_cache()
async def draw_majs_info_img(ev: Event, uid: str, mode: str = "auto"):
    MODE: bool = MAJS_CONFIG.get_config("UseFlowerHistory").data
    data4 = await majs_api.get_player_stats(uid)
    data3 = await majs_api.get_player_stats(uid, "3")

    extended4 = await majs_api.get_player_extended(uid)
    extended3 = await majs_api.get_player_extended(uid, "3")

    # from gsuid_core.utils.image.image_tools import get_avatar_with_ring
    # avatar = await get_avatar_with_ring(ev)

    if data4 == data3 or extended3 == extended4:
        return "不存在该ID的玩家数据...\n提示: 需要在金之间有一定数量的对局才能被正确记录！"

    if isinstance(data4, int):
        data4 = deepcopy(player_stats_zero)
    if isinstance(data3, int):
        data3 = deepcopy(player_stats_zero)

    if isinstance(extended4, int):
        extended4 = deepcopy(player_extend_zero)
    if isinstance(extended3, int):
        extended3 = deepcopy(player_extend_zero)

    if mode == "3" or (
        mode == "auto" and data4["level"]["score"] < data3["level"]["score"]
    ):
        _mode = "三麻战绩"
        data = data3
        extended = extended3
        record = await majs_api.get_player_record(uid, MODE="3")
    else:
        _mode = "四麻战绩"
        data = data4
        extended = extended4
        record = await majs_api.get_player_record(uid)

    for s in player_extend_zero:
        if s not in extended:
            extended[s] = player_extend_zero[s]

    if isinstance(record, int):
        record = []

    level4_score = data4["level"]["score"] + data4["level"]["delta"]
    level3_score = data3["level"]["score"] + data3["level"]["delta"]

    level4 = PlayerLevel(data4["level"]["id"], level4_score)
    level3 = PlayerLevel(data3["level"]["id"], level3_score)

    img = get_bg()
    detail_bg = Image.open(TEXTURE / "detail_bg.png")
    mid = Image.open(TEXTURE / "mid.png")

    detail_draw = ImageDraw.Draw(detail_bg)
    mid_draw = ImageDraw.Draw(mid)

    mid_draw.text((500, 40), _mode, W, majs_font(30), "mm")

    title = await draw_title(f'{data["nickname"]} · UID {uid}')
    img.paste(title, (0, 0), title)

    zm_rate = get_rate(extended["自摸率"])
    mt_rate = get_rate(extended["默听率"])
    lj_rate = get_rate(extended["流局率"])
    lt_rate = get_rate(extended["流听率"])
    fl_rate = get_rate(extended["副露率"])
    lz_rate = get_rate(extended["立直率"])

    hl_num = "{:.2f}".format(extended["和了巡数"])
    avg_score = str(extended["平均打点"])
    avg_chong = str(extended["平均铳点"])

    bf_rate = get_rate(data["negative_rate"])
    yf_rate = get_rate(extended["一发率"])
    jddxl = str(extended["净打点效率"])

    all_rong = (
        extended["立直和了"] + extended["副露和了"] + extended["默听和了"]
    )
    lz_r_rate = extended["立直和了"] / all_rong
    fl_r_rate = extended["副露和了"] / all_rong
    mt_r_rate = extended["默听和了"] / all_rong

    lz_f_rate = extended["放铳时立直率"]
    fl_f_rate = extended["放铳时副露率"]

    all_chong = (
        extended["放铳至立直"]
        + extended["放铳至副露"]
        + extended["放铳至默听"]
    )
    lz_c_rate = extended["放铳至立直"] / all_chong
    fl_c_rate = extended["放铳至副露"] / all_chong
    mt_c_rate = extended["放铳至默听"] / all_chong

    for index, _t in enumerate(
        [
            zm_rate,
            mt_rate,
            lj_rate,
            lt_rate,
            fl_rate,
            lz_rate,
            hl_num,
            avg_score,
            avg_chong,
            bf_rate,
            yf_rate,
            jddxl,
        ]
    ):
        detail_draw.text(
            (151 + 138 * (index % 6), 65 + 86 * (index // 6)),
            _t,
            W,
            majs_font(30),
            "mm",
        )

    record_bg = Image.open(TEXTURE / "record_bg.png")
    record_p = Image.new("RGBA", record_bg.size)
    record_draw = ImageDraw.Draw(record_bg)
    pos_prev = (0, 0)

    for i, r in enumerate(record[::-1]):
        ranks = {p["nickname"]: p["gradingScore"] for p in r["players"]}
        sorted_players = sorted(
            ranks.items(), key=lambda x: x[1], reverse=True
        )
        _rank = next(
            (
                i + 1
                for i, (player, score) in enumerate(sorted_players)
                if player == data["nickname"]
            )
        )
        if MODE:
            flower = Image.open(TEXTURE / "flower.png")
            if _rank > 1:
                _rank_alpha = RANK_ALPHA[_rank]
                flower.putalpha(
                    flower.getchannel("A").point(
                        lambda x: round(x * _rank_alpha) if x > 0 else 0
                    )
                )
            detail_bg.paste(
                flower, (99 + 99 * (i % 8), 588 + 152 * (i // 8)), flower
            )
            detail_draw.text(
                (149 + 99 * (i % 8), 710 + 152 * (i // 8)),
                f"第{_rank}名",
                W,
                majs_font(24),
                "mm",
            )
        else:
            pos_y = RANK_POS[_rank]
            pos = (108 + i * 50, pos_y)
            rank_img = Image.open(TEXTURE / f"rank_{_rank}.png")
            if pos_prev != (0, 0):
                line_pos = (
                    (pos_prev[0] + 15, pos_prev[1] + 15),
                    (pos[0] + 15, pos[1] + 15),
                )
                record_draw.line(line_pos, W, 3)
            record_p.paste(rank_img, pos, rank_img)
            pos_prev = pos

    if not MODE:
        record_bg.paste(record_p, (0, 0), record_p)
        detail_bg.paste(record_bg, (0, 558), record_bg)
        detail_draw.text(
            (500, 590),
            "最近对局记录走势",
            W,
            majs_font(34),
            "mm",
        )

    footer = get_footer()

    rank4_icon = await get_rank_icon(level4, data4, extended4, "4")
    rank3_icon = await get_rank_icon(level3, data3, extended3, "3")
    char_card = await get_char_card()

    lz_rong = await get_lz_bar("rong", lz_r_rate, fl_r_rate, mt_r_rate)
    lz_chong = await get_lz_bar("chong", lz_f_rate, fl_f_rate)
    lz_chongz = await get_lz_bar("chong_to", lz_c_rate, fl_c_rate, mt_c_rate)

    detail_bg.paste(lz_rong, (0, 238), lz_rong)
    detail_bg.paste(lz_chong, (0, 328), lz_chong)
    detail_bg.paste(lz_chongz, (0, 418), lz_chongz)

    img.paste(char_card, (34, 518), char_card)
    img.paste(rank4_icon, (357, 545), rank4_icon)
    img.paste(rank3_icon, (357, 857), rank3_icon)

    img.paste(detail_bg, (0, 1188), detail_bg)
    img.paste(mid, (0, 1161), mid)
    img.paste(footer, (0, 2151), footer)

    return await convert_img(img)


async def get_lz_bar(
    title: str, v1: float, v2: float, v3: Optional[float] = None
):
    if v3 is None:
        v3 = 1 - v1 - v2

    bar = Image.open(TEXTURE / f"lz_{title}.png")
    lz_draw = ImageDraw.Draw(bar)

    start = 102
    y1, y2 = 51, 81

    x2 = start + int(770 * v1)
    x3 = x2 + int(770 * v2) + 10
    x4 = x3 + int(770 * v3) + 10

    c1 = (157, 157, 212)
    c2 = (157, 212, 192)
    c3 = (212, 157, 185)

    lz_draw.rounded_rectangle(((start, y1), (x2, y2)), radius=5, fill=c1)
    lz_draw.rounded_rectangle(((x2 + 10, y1), (x3, y2)), radius=5, fill=c2)
    lz_draw.rounded_rectangle(((x3 + 10, y1), (x4, y2)), radius=5, fill=c3)

    return bar


def get_rate(value: float):
    if not value:
        return "0.00%"
    return "{:.2f}%".format(value * 100)


async def get_char_card():
    char_bg = Image.open(TEXTURE / "char_bg.png")
    char_fg = Image.open(TEXTURE / "char_fg.png")
    char = Image.open(TEXTURE / "waitingroom.png").resize((289, 617))
    char_bg.paste(char, (38, 37), char)
    char_bg.paste(char_fg, (0, 0), char_fg)
    return char_bg


async def get_rank_img(
    major_rank: str, minor_rank: int, mode: str = "4", size: int = 156
):
    img = Image.new('RGBA', (156, 156))

    rank_icon = Image.open(TEXTURE / f"{major_rank}_{mode}.png")
    rank_icon = rank_icon.resize((128, 128)).convert("RGBA")

    img.paste(rank_icon, (14, 7), rank_icon)

    if major_rank != "魂天":
        for i in range(3):
            if minor_rank > i:
                img.paste(star_full, (26 + i * 38, 118), star_full)
            else:
                img.paste(star_empty, (26 + i * 38, 118), star_empty)

    if size != 156:
        img = img.resize((size, size))

    return img


async def get_rank_icon(
    level: PlayerLevel, stats: Stats, extended: Extended, mode: str = "4"
):
    rankbg = Image.open(TEXTURE / "rank_bg.png")
    rank_icon = await get_rank_img(
        level.major_rank,
        level.minor_rank,
        mode,
        156,
    )

    rankbg.paste(rank_icon, (51, 28), rank_icon)

    rank_draw = ImageDraw.Draw(rankbg)
    avg_rank = "{:.2f}".format(stats["avg_rank"])
    first_rate = get_rate(stats["rank_rates"][0])
    rong_rate = get_rate(extended["和牌率"])
    chong_rate = get_rate(extended["放铳率"])

    rank_draw.text((296, 78), level.full_tag, W, majs_font(44), "mm")
    rank_draw.text((460, 78), level.real_display_score, G, majs_font(28), "mm")

    rank_draw.text((282, 146), str(stats["count"]), W, majs_font(36), "lm")
    rank_draw.text((458, 146), avg_rank, W, majs_font(36), "lm")

    rank_draw.text((155, 239), first_rate, W, majs_font(32), "mm")
    rank_draw.text((300, 239), rong_rate, W, majs_font(32), "mm")
    rank_draw.text((445, 239), chong_rate, W, majs_font(32), "mm")

    return rankbg
