from typing import List
from pathlib import Path

from PIL import Image, ImageDraw
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.image.image_tools import crop_center_img
from gsuid_core.utils.fonts.fonts import core_font as majs_font

from ..utils.map import lqc, extend_res
from .majsoul_friend import MajsoulFriend
from ..utils.image import get_bg, add_footer
from ..utils.resource.get_res import get_charactor_img
from ..utils.resource.RESOURCE_PATH import CHARACTOR_PATH
from ..majs_info.draw_majs_info_pic import draw_title, get_rank_img

TEXT_PATH = Path(__file__).parent / 'texture2d'


async def draw_friend_rank_img(friends: List[MajsoulFriend], mode: str = '4'):
    friends = friends[:14]
    img = crop_center_img(get_bg(), 1000, 600 + len(friends) * 153)
    title = await draw_title(f'雀魂好友{mode}麻排行榜')
    img.paste(title, (0, 0), title)

    mask = Image.open(TEXT_PATH / 'mask.png')
    for index, friend in enumerate(friends):
        if mode == '4':
            level = friend.level
            score = level.formatAdjustedScore(friend.level_score)
        else:
            level = friend.level3
            score = level.formatAdjustedScore(friend.level3_score)

        bar = Image.open(TEXT_PATH / 'bar.png')
        bar_draw = ImageDraw.Draw(bar)

        avatar_id = str(friend.avatar_id)
        path = lqc[avatar_id]['path']
        name_path: str = path.split('/')[-1]
        local_path: Path = CHARACTOR_PATH / name_path / 'bighead.png'

        if local_path.exists():
            avatar = Image.open(local_path)
        else:
            path = path + '/bighead.png'
            if path in extend_res:
                path_prefix = extend_res[path]
            else:
                for i in extend_res:
                    if path in i:
                        path_prefix = extend_res[i]
                        path = i
                        break
                else:
                    path_prefix = 'v0.11.14.w'
            url = f'https://game.maj-soul.com/1/{path_prefix}/{path}'
            avatar = await get_charactor_img(url, local_path)

        avatar = avatar.resize((128, 128))
        bar.paste(avatar, (69, 15), mask)

        rank_img = await get_rank_img(
            level.get_label(),
            level.minor_rank,
            mode,
            94,
        )

        r = f'{level.get_label()}{level.minor_rank}'
        nickname = friend.nickname
        bar.paste(rank_img, (234, 32), rank_img)

        bar_draw.text((355, 80), nickname, 'white', majs_font(34), 'lm')
        bar_draw.text((653, 80), r, 'white', majs_font(44), 'mm')
        bar_draw.text((817, 80), score, 'white', majs_font(30), 'mm')

        img.paste(bar, (0, 538 + index * 153), bar)

    img = add_footer(img)
    res = await convert_img(img)
    return res
