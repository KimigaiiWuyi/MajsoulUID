from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.message_models import Button
from gsuid_core.utils.message import send_diff_msg

from ..utils.database.models import MajsBind
from .search_player import search_player_with_name

majs_user_bind = SV('雀魂用户绑定')


@majs_user_bind.on_command(
    (
        '绑定uid',
        '绑定UID',
        '绑定',
        '切换uid',
        '切换UID',
        '切换',
        '删除uid',
        '删除UID',
    ),
    block=True,
)
async def send_majs_bind_uid_msg(bot: Bot, ev: Event):
    uid = ev.text.strip()

    if not uid and '绑定' in ev.command:
        return await bot.send(
            '该命令需要带上正确的uid!\n如果不知道, 可以使用雀魂搜索命令查询\n如雀魂搜索Wuyi'
        )

    await bot.logger.info('[Majs] 开始执行[绑定/解绑用户信息]')
    qid = ev.user_id
    await bot.logger.info('[Majs] [绑定/解绑]UserID: {}'.format(qid))

    if uid and not uid.isdigit():
        return await bot.send('你输入了错误的格式!')

    if '绑定' in ev.command:
        data = await MajsBind.insert_uid(qid, ev.bot_id, uid, ev.group_id)
        return await send_diff_msg(
            bot,
            data,
            {
                0: f'[Majs] 绑定UID{uid}成功！',
                -1: f'[Majs] UID{uid}的位数不正确！',
                -2: f'[Majs] UID{uid}已经绑定过了！',
                -3: '[Majs] 你输入了错误的格式!',
            },
        )
    elif '切换' in ev.command:
        retcode = await MajsBind.switch_uid_by_game(qid, ev.bot_id, uid)
        if retcode == 0:
            return await bot.send(f'[Majs] 切换UID{uid}成功！')
        elif retcode == -3:
            now_uid = await MajsBind.get_uid_by_game(qid, ev.bot_id)
            if now_uid:
                return await bot.send(
                    f'[Majs] 你目前只绑定了一个UID{now_uid}, 无法切换!'
                )
            else:
                return await bot.send('[Majs] 你尚未绑定任何UID, 无法切换!')
        else:
            return await bot.send(f'[Majs] 尚未绑定该UID{uid}')
    else:
        data = await MajsBind.delete_uid(qid, ev.bot_id, uid)
        return await send_diff_msg(
            bot,
            data,
            {
                0: f'[Majs] 删除UID{uid}成功！',
                -1: f'[Majs] 该UID{uid}不在已绑定列表中！',
            },
        )


@majs_user_bind.on_command(('搜索'), block=True)
async def send_majs_search_msg(bot: Bot, ev: Event):
    im, uid_list = await search_player_with_name(ev.text.strip())
    if uid_list:
        buttons = [Button(f'✏️绑定{uid}', f'雀魂绑定{uid}') for uid in uid_list]
    else:
        buttons = None
    await bot.send_option(im, buttons)
