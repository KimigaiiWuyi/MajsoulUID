from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.utils.database.api import get_uid

from .majsoul import manager
from ..utils.error_reply import UID_HINT
from ..utils.database.models import MajsBind, MajsPush, MajsUser

majsoul_notify = SV('雀魂推送服务', pm=0)
majsoul_friend_level_billboard = SV('雀魂好友排行榜')
majsoul_get_notify = SV('雀魂订阅推送')
majsoul_add_account = SV('雀魂账号池', pm=0)


@majsoul_add_account.on_command(('雀魂添加账号'))
async def majsoul_add_at(bot: Bot, ev: Event):
    access_token = ev.text.strip()
    if not access_token:
        return await bot.send('❌ 请输入有效的access_token!')

    account_id = await manager.check_access_token(access_token)
    if isinstance(account_id, bool):
        return await bot.send('❌ 登陆失败, 请输入正确的access_token!')

    if MajsUser.data_exist(uid=account_id):
        await MajsUser.update_data_by_data(
            {'uid': str(account_id)}, {'cookie': access_token}
        )
    else:
        await MajsUser.insert_data(
            ev.user_id,
            ev.bot_id,
            uid=str(account_id),
            cookie=access_token,
        )

    conn = await manager.start()
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = '🥰成功向账号池添加账号！尝试自动连接中...\n'

    msg += f'当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}'
    await bot.send(msg)


@majsoul_get_notify.on_command(('雀魂订阅'))
async def majsoul_get_notify_command(bot: Bot, ev: Event):
    conn = await manager.start()
    if isinstance(conn, str):
        return await bot.send(conn)

    uid = await get_uid(bot, ev, MajsBind)
    if uid is None:
        return await bot.send(UID_HINT)

    await conn.fetchInfo()
    for friend in conn.friends:
        if uid == str(friend.account_id):
            break
    else:
        return await bot.send(
            '[majs] 未找到好友信息! \n'
            f'请先在【游戏中】添加 {conn.nick_name}: {conn.account_id}好友再执行此操作！'
        )

    push_id = ev.group_id if ev.group_id else 'on'
    if MajsPush.data_exist(uid=uid):
        retcode = await MajsPush.update_data_by_uid(
            uid,
            ev.bot_id,
            push_id=push_id,
        )
    else:
        retcode = await MajsPush.full_insert_data(
            uid=uid,
            user_id=ev.user_id,
            push_id=push_id,
        )

    if retcode == 0:
        return await bot.send(f'[majs] 修改推送订阅成功！当前值：{push_id}')
    else:
        return await bot.send('[majs] 推送订阅失败！')


@majsoul_notify.on_fullmatch(('雀魂推送启动'))
async def majsoul_notify_command(bot: Bot, event: Event):
    conn = await manager.start()
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = '🥰成功启动雀魂订阅消息推送服务！\n'

    msg += f'当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}'
    await bot.send(msg)


@majsoul_notify.on_fullmatch('雀魂重启订阅服务')
async def majsoul_notify_reset_command(bot: Bot, event: Event):
    conn = await manager.restart()
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = '🥰成功重启雀魂订阅消息推送服务！\n'
    msg += f'当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}'
    await bot.send(msg)


@majsoul_notify.on_fullmatch('雀魂检查服务')
async def majsoul_notify_check_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send('未找到有效连接, 请先进行[雀魂推送启动]')

    if await conn.check_alive():
        msg = '雀魂服务连接正常！\n'
        msg += f'当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}'
    else:
        msg = '雀魂服务连接失败, 请先进行[雀魂重启订阅服务]'
    await bot.send(msg)


@majsoul_friend_level_billboard.on_command('雀魂好友排行榜')
async def majsoul_friend_billboard_command(bot: Bot, event: Event):
    # get connection
    conn = manager.get_conn()
    if conn is None:
        return await bot.send('未找到有效连接, 请先进行[雀魂推送启动]')
    # get friends
    friends = conn.friends
    if '四' in event.text:
        # sort by level.id and level.score
        friends.sort(key=lambda x: (x.level.id, x.level.score), reverse=True)
        # get level info
        msg = '本群雀魂好友四麻排行榜\n'
        for friend in friends:
            level_str = friend.level.formatAdjustedScoreWithTag(
                friend.level.score
            )
            msg += f'{friend.nickname} {level_str}\n'
        await bot.send(msg)
    else:
        friends.sort(key=lambda x: (x.level3.id, x.level3.score), reverse=True)
        msg = '本群雀魂好友三麻排行榜\n'
        for friend in friends:
            level_str = friend.level3.formatAdjustedScoreWithTag(
                friend.level3.score
            )
            msg += f'{friend.nickname} {level_str}\n'
        await bot.send(msg)
