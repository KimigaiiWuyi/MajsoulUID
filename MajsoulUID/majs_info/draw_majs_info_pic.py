from pathlib import Path
from copy import deepcopy
from typing import Optional

from PIL import Image, ImageDraw
from gsuid_core.models import Event
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.fonts.fonts import core_font as majs_font

from ..utils.majs_api import majs_api
from ..utils.api.remote import PlayerLevel
from ..utils.api.models import Stats, Extended
from ..utils.api.remote_const import player_stats_zero, player_extend_zero

TEXTURE = Path(__file__).parent / "texture2d"
star_empty = Image.open(TEXTURE / 'star_empty.png').resize((32, 32))
star_full = Image.open(TEXTURE / 'star_full.png').resize((32, 32))

RANK_ALPHA = {2: 0.7, 3: 0.5, 4: 0.1}

W = (255, 255, 255)


async def draw_majs_info_img(ev: Event, uid: str):
    data4 = await majs_api.get_player_stats(uid)
    data3 = await majs_api.get_player_stats(uid, '3')

    extended4 = await majs_api.get_player_extended(uid)
    extended3 = await majs_api.get_player_extended(uid, '3')

    # from gsuid_core.utils.image.image_tools import get_avatar_with_ring
    # avatar = await get_avatar_with_ring(ev)

    if data4 == data3 or extended3 == extended4:
        return '不存在该ID的玩家数据...\n提示: 需要在金之间有一定数量的对局才能被正确记录！'

    if isinstance(data4, int):
        data4 = deepcopy(player_stats_zero)
    if isinstance(data3, int):
        data3 = deepcopy(player_stats_zero)

    if isinstance(extended4, int):
        extended4 = deepcopy(player_extend_zero)
    if isinstance(extended3, int):
        extended3 = deepcopy(player_extend_zero)

    if data4['level']['id'] >= data3['level']['id']:
        data = data4
        extended = extended4
        record = await majs_api.get_player_record(uid)
    else:
        data = data3
        extended = extended3
        record = await majs_api.get_player_record(uid, MODE='3')

    for s in player_extend_zero:
        if s not in extended:
            extended[s] = player_extend_zero[s]

    if isinstance(record, int):
        record = []

    level4 = PlayerLevel(data4['level']['id'])
    level3 = PlayerLevel(data3['level']['id'])

    img = Image.open(TEXTURE / 'bg.jpg')
    title = Image.open(TEXTURE / 'title.png')
    detail_bg = Image.open(TEXTURE / 'detail_bg.png')

    detail_draw = ImageDraw.Draw(detail_bg)
    title_draw = ImageDraw.Draw(title)

    title_draw.text(
        (504, 435),
        f'{data["nickname"]} · UID {uid}',
        W,
        majs_font(30),
        'mm',
    )
    img.paste(title, (0, 0), title)

    zm_rate = get_rate(extended['自摸率'])
    mt_rate = get_rate(extended['默听率'])
    lj_rate = get_rate(extended['流局率'])
    lt_rate = get_rate(extended['流听率'])
    fl_rate = get_rate(extended['副露率'])
    lz_rate = get_rate(extended['立直率'])

    hl_num = '{:.2f}'.format(extended['和了巡数'])
    avg_score = str(extended['平均打点'])
    avg_chong = str(extended['平均铳点'])

    bf_rate = get_rate(data['negative_rate'])
    yf_rate = get_rate(extended['一发率'])
    jddxl = str(extended['净打点效率'])

    all_rong = (
        extended['立直和了'] + extended['副露和了'] + extended['默听和了']
    )
    lz_r_rate = extended['立直和了'] / all_rong
    fl_r_rate = extended['副露和了'] / all_rong
    mt_r_rate = extended['默听和了'] / all_rong

    lz_f_rate = extended['放铳时立直率']
    fl_f_rate = extended['放铳时副露率']

    all_chong = (
        extended['放铳至立直']
        + extended['放铳至副露']
        + extended['放铳至默听']
    )
    lz_c_rate = extended['放铳至立直'] / all_chong
    fl_c_rate = extended['放铳至副露'] / all_chong
    mt_c_rate = extended['放铳至默听'] / all_chong

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
            'mm',
        )

    for i, r in enumerate(record):
        flower = Image.open(TEXTURE / 'flower.png')
        ranks = {p['nickname']: p['gradingScore'] for p in r['players']}
        sorted_players = sorted(
            ranks.items(), key=lambda x: x[1], reverse=True
        )
        _rank = next(
            (
                i + 1
                for i, (player, score) in enumerate(sorted_players)
                if player == data['nickname']
            )
        )
        if _rank > 1:
            _rank_alpha = RANK_ALPHA[_rank]
            flower.putalpha(
                flower.getchannel('A').point(
                    lambda x: round(x * _rank_alpha) if x > 0 else 0
                )
            )
        detail_bg.paste(
            flower, (99 + 99 * (i % 8), 588 + 152 * (i // 8)), flower
        )
        detail_draw.text(
            (149 + 99 * (i % 8), 710 + 152 * (i // 8)),
            f'第{_rank}名',
            W,
            majs_font(24),
            'mm',
        )

    footer = Image.open(TEXTURE / 'footer.png')

    rank4_icon = await get_rank_icon(level4, data4, extended4, '4')
    rank3_icon = await get_rank_icon(level3, data3, extended3, '3')
    char_card = await get_char_card()

    lz_rong = await get_lz_bar('rong', lz_r_rate, fl_r_rate, mt_r_rate)
    lz_chong = await get_lz_bar('chong', lz_f_rate, fl_f_rate)
    lz_chongz = await get_lz_bar('chong_to', lz_c_rate, fl_c_rate, mt_c_rate)

    detail_bg.paste(lz_rong, (0, 238), lz_rong)
    detail_bg.paste(lz_chong, (0, 328), lz_chong)
    detail_bg.paste(lz_chongz, (0, 418), lz_chongz)

    img.paste(char_card, (34, 518), char_card)
    img.paste(rank4_icon, (357, 545), rank4_icon)
    img.paste(rank3_icon, (357, 857), rank3_icon)

    img.paste(detail_bg, (0, 1188), detail_bg)
    img.paste(footer, (0, 2151), footer)

    return await convert_img(img)


async def get_lz_bar(
    title: str, v1: float, v2: float, v3: Optional[float] = None
):
    if v3 is None:
        v3 = 1 - v1 - v2

    bar = Image.open(TEXTURE / f'lz_{title}.png')
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
        return '0.00%'
    return '{:.2f}%'.format(value * 100)


async def get_char_card():
    char_bg = Image.open(TEXTURE / 'char_bg.png')
    char_fg = Image.open(TEXTURE / 'char_fg.png')
    char = Image.open(TEXTURE / 'waitingroom.png').resize((289, 617))
    char_bg.paste(char, (38, 37), char)
    char_bg.paste(char_fg, (0, 0), char_fg)
    return char_bg


async def get_rank_icon(
    level: PlayerLevel, stats: Stats, extended: Extended, mode: str = '4'
):
    rankbg = Image.open(TEXTURE / 'rank_bg.png')
    rank_icon = Image.open(TEXTURE / f'{level.major_rank}_{mode}.png')
    rank_icon = rank_icon.resize((128, 128)).convert('RGBA')

    rankbg.paste(rank_icon, (65, 30), rank_icon)

    if level.major_rank != '魂天':
        for i in range(3):
            if level.minor_rank > i:
                rankbg.paste(star_full, (82 + i * 30, 138), star_full)
            else:
                rankbg.paste(star_empty, (82 + i * 30, 138), star_empty)

    rank_draw = ImageDraw.Draw(rankbg)
    avg_rank = '{:.2f}'.format(stats['avg_rank'])
    first_rate = get_rate(stats['rank_rates'][0])
    rong_rate = get_rate(extended['和牌率'])
    chong_rate = get_rate(extended['放铳率'])

    rank_draw.text((324, 78), level.full_tag, W, majs_font(44), 'mm')
    rank_draw.text((282, 146), str(stats['count']), W, majs_font(36), 'lm')
    rank_draw.text((458, 146), avg_rank, W, majs_font(36), 'lm')

    rank_draw.text((155, 239), first_rate, W, majs_font(32), 'mm')
    rank_draw.text((300, 239), rong_rate, W, majs_font(32), 'mm')
    rank_draw.text((445, 239), chong_rate, W, majs_font(32), 'mm')

    return rankbg
