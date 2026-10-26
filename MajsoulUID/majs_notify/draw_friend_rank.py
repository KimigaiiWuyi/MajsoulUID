from typing import List, Union
from pathlib import Path

from PIL import Image, ImageDraw

from gsuid_core.logger import logger
from gsuid_core.utils.fonts.fonts import core_font as majs_font
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.image.image_tools import crop_center_img

from ._level import MajsoulLevel
from ..utils.map import BIGHEAD_PATH, lqc
from ..utils.image import get_bg, add_footer
from .majsoul_friend import MajsoulFriend
from ..majs_info.draw_majs_info_pic import draw_title, get_rank_img

TEXT_PATH = Path(__file__).parent / "texture2d"
mask = Image.open(TEXT_PATH / "mask.png")
DEFAULT_SKIN_ID = "400000"


def get_bighead(avatar_id: str) -> Image.Image:
    path = BIGHEAD_PATH / f"{avatar_id}.webp"
    if avatar_id not in lqc or not path.exists():
        logger.info(f"[majs] 找不到皮肤 {avatar_id} 的头像, 采用默认头像")
        path = BIGHEAD_PATH / f"{DEFAULT_SKIN_ID}.webp"
    return Image.open(path)


async def draw_bar(
    avatar_id: Union[int, str],
    nickname: str,
    level: MajsoulLevel,
    score: str,
    mode: str = "4",
):
    bar = Image.open(TEXT_PATH / "bar.png")
    bar_draw = ImageDraw.Draw(bar)

    avatar = get_bighead(str(avatar_id)).resize((128, 128))
    bar.paste(avatar, (69, 15), mask)

    rank_img = await get_rank_img(
        level.get_label(),
        level.minor_rank,
        mode,
        94,
    )

    r = f"{level.get_label()}{level.minor_rank}"
    bar.paste(rank_img, (234, 32), rank_img)

    bar_draw.text((355, 80), nickname, "white", majs_font(34), "lm")
    bar_draw.text((653, 80), r, "white", majs_font(44), "mm")
    bar_draw.text((817, 80), score, "white", majs_font(30), "mm")
    return bar


async def draw_friend_rank_img(friends: List[MajsoulFriend], mode: str = "4"):
    friends = friends[:14]
    img = crop_center_img(get_bg(), 1000, 600 + len(friends) * 153)
    title = await draw_title(f"雀魂好友{mode}麻排行榜")
    img.paste(title, (0, 0), title)

    for index, friend in enumerate(friends):
        if mode == "4":
            level = friend.level
            score = level.formatAdjustedScore(friend.level_score)
        else:
            level = friend.level3
            score = level.formatAdjustedScore(friend.level3_score)

        bar = await draw_bar(friend.avatar_id, friend.nickname, level, score, mode)

        img.paste(bar, (0, 538 + index * 153), bar)

    img = add_footer(img)
    res = await convert_img(img)
    return res
    return res
    return res
