from typing import List
from pathlib import Path

from PIL import Image, ImageDraw
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.image.image_tools import crop_center_img
from gsuid_core.utils.fonts.fonts import core_font as majs_font

from ._level import MajsoulLevel
from .draw_friend_rank import draw_bar
from .check_reach import find_ting_tiles
from ..utils.image import get_bg, add_footer

TEXT_PATH = Path(__file__).parent / 'texture2d_review'
PAI_PATH = TEXT_PATH / 'pai'

kyoku_map = {
    0: '东',
    1: '南',
    2: '西',
    3: '北',
}

_type_map = {
    'dahai': '打',
    'ankan': '暗杠',
    'tsumo': '自摸',
    'ron': '荣和',
    'reach': '立直',
    'ronpinfu': '加飘',
    'daburi': '打切',
    'none': '放弃',
}

target_map = {
    1: '上家',
    2: '对家',
    3: '下家',
}


def get_diff(a: int, b: int):
    # 假设a和b的范围是[0, 1, 2, 3]
    diff = 0
    while a != b:
        a = (a - 1) % 4
        diff += 1
    return target_map.get(diff, '未知')


def get_color(rate: float):
    if rate <= 0.65:
        color = (255, 0, 0)
    elif rate <= 0.75:
        color = (255, 161, 0)
    elif rate >= 0.91:
        color = (74, 255, 0)
    else:
        color = (255, 255, 255)
    return color


async def draw_review_info_img(
    tenhou_log: dict,
    data: dict,
    kyoku_id: int = 0,
):
    try:
        kyokus: dict = data["data"]["review"]["kyokus"][kyoku_id]
        head: List[dict] = tenhou_log['head']['accounts']
    except IndexError:
        return f"该Game未存在该局ID：{kyoku_id}"

    kyoku: int = kyokus["kyoku"] % 4
    kyoku_str = kyoku_map[kyoku]

    w, h = 2800, 964 + 100

    h_num = ((len(kyokus['entries']) - 1) // 2) + 1
    h += h_num * 255

    img = crop_center_img(get_bg(), w, h)

    title = Image.open(TEXT_PATH / 'title.png')
    actor_file = Image.open(TEXT_PATH / 'actor_file.png')
    spliter = Image.open(TEXT_PATH / 'spliter.png')
    spliter_draw = ImageDraw.Draw(spliter)
    spliter_draw.text(
        (1400, 35),
        f"【{kyoku_str}{kyokus['honba']+1}局】",
        font=majs_font(50),
        fill=(255, 255, 255),
        anchor='mm',
    )

    img.paste(title, (0, 0), title)
    actor: dict = head[0]
    level = MajsoulLevel(actor['level']['id'])
    bar = await draw_bar(
        actor['avatar_id'],
        actor['nickname'],
        level,
        level.formatAdjustedScore(actor['level']['score']),
    )

    bar = bar.resize((1450, 222))
    actor_file.paste(bar, (-27, 106), bar)

    img.paste(actor_file, (0, 396), actor_file)
    img.paste(spliter, (0, 800), spliter)

    ai_frame = Image.open(TEXT_PATH / 'ai.png')
    aciton_frame = Image.open(TEXT_PATH / 'action.png')
    mo_frame = Image.open(TEXT_PATH / 'mo.png')
    hora_frame = Image.open(TEXT_PATH / 'hora.png')

    total_reviewed = data["data"]["review"]['total_reviewed']
    total_matches = data["data"]["review"]['total_matches']

    now_reviewed = 0
    now_matches = 0
    now_warning = 0

    actor_id = 0
    for index, en in enumerate(kyokus['entries']):
        now_reviewed += 1
        tehai: List[str] = en['state']['tehai']
        fuuros: List[dict] = en['state']['fuuros']
        ai: dict = en['expected']
        actual: dict = en['actual']
        now_pai: str = en['tile']
        last_actor: int = en['last_actor']

        if 'actor' in actual:
            actor_id: int = actual['actor']

        actual_type = actual['type']
        ai_type = ai['type']

        ai_dehai: str = ai['pai'] if 'pai' in ai else ''
        actual_dehai: str = actual['pai'] if 'pai' in actual else ''

        ai_str = f"AI选择: {_type_map.get(ai_type, '未知')} {ai_dehai}"
        actual_str = (
            f"你选择: {_type_map.get(actual_type, '未知')} {actual_dehai}"
        )
        cond_str = f"{ai_str}  |  {actual_str}"

        if actual_type == 'hora':
            frame = hora_frame
            frame_str = '胡牌'
        elif actual_type != 'dahai' and actual_type != 'ankan':
            frame = aciton_frame
            target_str = get_diff(actor_id, last_actor)
            frame_str = f'{target_str}出牌'
        else:
            frame = mo_frame
            frame_str = '自己摸到'

        if ai == actual:
            en_bg = Image.open(TEXT_PATH / 'yes.png')
            now_matches += 1
        else:
            for proba in en['details']:
                if proba['action'] == actual and proba['prob'] >= 0.6:
                    en_bg = Image.open(TEXT_PATH / 'warning.png')
                    now_warning += 1
                    break
            else:
                en_bg = Image.open(TEXT_PATH / 'no.png')

        en_bg_draw = ImageDraw.Draw(en_bg)
        en_bg_draw.text(
            (232, 27),
            cond_str,
            font=majs_font(24),
            fill=(255, 255, 255),
            anchor='lm',
        )

        en_bg_draw.text(
            (111, 27),
            f"【第{index}巡】",
            font=majs_font(24),
            fill=(255, 255, 255),
            anchor='lm',
        )

        if actual_type == 'ankan':
            actual_pai: str = actual['consumed'][0]
        elif actual_type == 'hora':
            actual_pai = now_pai
        elif actual_type == 'reach':
            if 'pai' not in actual:
                actual_pai = list(find_ting_tiles(tehai).keys())[0]
            else:
                actual_pai = actual['pai']
        elif actual_type != 'none':
            actual_pai = actual['pai']
        else:
            actual_pai = 'none'

        if ai_type == 'ankan':
            ai_pai: str = ai['consumed'][0]
        elif ai_type == 'hora':
            ai_pai = now_pai
        elif ai_type == 'reach':
            if 'pai' not in ai:
                ai_pai = list(find_ting_tiles(tehai).keys())[0]
            else:
                ai_pai = ai['pai']
        elif ai_type != 'none':
            ai_pai = ai['pai']
        else:
            ai_pai = 'none'

        x_tile = 0
        is_ai = False
        is_actual = False
        for hindex, hai in enumerate(tehai):
            y = 83

            hai_img = Image.open(PAI_PATH / f'{hai}.png')

            if hai == ai_pai and not is_ai:
                hai_img.paste(ai_frame, (0, 0), ai_frame)
                is_ai = True

            if hai == actual_pai and not is_actual:
                y -= 28
                en_bg_draw.text(
                    (128 + x_tile, 236),
                    "▲ 你",
                    font=majs_font(24),
                    fill=(255, 255, 255),
                    anchor='mm',
                )
                is_actual = True

            en_bg.paste(hai_img, (88 + x_tile, y), hai_img)
            x_tile += 81

        y = 83
        x_tile = 1170
        for findex, fuuro in enumerate(fuuros):
            # _fuuro_type: str = fuuro['type']
            if 'pai' in fuuro:
                pais: List[str] = [fuuro['pai']]
            else:
                pais = []
            pais.extend(fuuro['consumed'])

            if 'target' in fuuro:
                fuuro_target: int = fuuro['target']
                rotate: int = (fuuro_target + 4 - actor_id) % 4
            else:
                rotate = 0

            for pindex, _fuuro_pai in enumerate(pais):
                _fuuro_pai_img = Image.open(PAI_PATH / f'{_fuuro_pai}.png')
                _fuuro_pai_img = _fuuro_pai_img.resize((57, 91))
                if (
                    (rotate == 3 and pindex == len(pais) - 1)
                    or (rotate == 1 and pindex == 0)
                    or (rotate == 2 and pindex == 1)
                ):
                    _fuuro_pai_img = _fuuro_pai_img.rotate(90, expand=True)
                    _fuuro_y = 155
                    x_tile -= 34
                    en_bg.paste(
                        _fuuro_pai_img, (x_tile, _fuuro_y), _fuuro_pai_img
                    )
                    x_tile -= 46
                else:
                    _fuuro_y = 121
                    en_bg.paste(
                        _fuuro_pai_img, (x_tile, _fuuro_y), _fuuro_pai_img
                    )
                    x_tile -= 57

            x_tile -= 10

        now_hai_img = Image.open(PAI_PATH / f'{now_pai}.png')
        now_hai_img.paste(frame, (0, 0), frame)
        en_bg.paste(now_hai_img, (1265, 83), now_hai_img)
        en_bg_draw.text(
            (1265, 236),
            frame_str,
            font=majs_font(24),
            fill=(255, 255, 255),
            anchor='mm',
        )

        if index < h_num:
            _x = 0
        else:
            _x = 1400

        img.paste(en_bg, (_x, 900 + ((index % h_num) * 255)), en_bg)

    total_rating = f'{(total_matches / total_reviewed) * 100:.2f}%'
    now_rating = f'{(now_matches / now_reviewed) * 100:.2f}%'

    total_str = f'{total_matches} / {total_reviewed}'
    now_str = f'{now_matches} / {now_reviewed}'
    now_w_str = f'{now_warning} / {now_reviewed}'
    now_score = (now_warning * 0.6 + now_matches) / now_reviewed
    now_score_str = f'{now_score * 100:.2f}%'

    total_color = get_color(total_matches / total_reviewed)
    now_color = get_color(now_matches / now_reviewed)
    now_score_color = get_color(now_score)

    review_info = Image.open(TEXT_PATH / 'review_info.png')
    review_draw = ImageDraw.Draw(review_info)

    data_map = (
        (now_score_str, now_score_color),
        (now_rating, now_color),
        (now_str, (74, 255, 0)),
        (now_w_str, (255, 161, 0)),
        (total_rating, total_color),
        (total_str, total_color),
    )

    for index, i in enumerate(data_map):
        c = i[1]
        text = i[0]
        review_draw.text(
            (int(170 + index * 209.4), 200),
            text,
            font=majs_font(40),
            fill=c,
            anchor='mm',
        )

    img.paste(review_info, (1390, 396), review_info)

    img = add_footer(img)
    r = await convert_img(img)
    return r
