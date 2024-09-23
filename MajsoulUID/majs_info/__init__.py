from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.sv import SV
from gsuid_core.utils.database.api import get_uid

from ..utils.database.models import MajsBind
from ..utils.error_reply import UID_HINT
from .draw_majs_info_pic import draw_majs_info_img

majs_user_info = SV("雀魂用户信息查询")


@majs_user_info.on_command(("查询"), block=True)
async def send_majs_search_msg(bot: Bot, ev: Event):
    uid = await get_uid(bot, ev, MajsBind)
    if uid is None:
        return await bot.send(UID_HINT)

    if "四" in ev.text:
        mode = "4"
    elif "三" in ev.text:
        mode = "3"
    else:
        mode = "auto"
    im = await draw_majs_info_img(ev, uid, mode)
    await bot.send(im)
