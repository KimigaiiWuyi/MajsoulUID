from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.sv import SV
from gsuid_core.utils.database.api import get_uid

from ..utils.api.remote import encode_account_id2
from ..utils.database.models import MajsBind, MajsPush, MajsUser
from ..utils.error_reply import UID_HINT
from .majsoul import manager

majsoul_notify = SV("雀魂推送服务", pm=0)
majsoul_friend_level_billboard = SV("雀魂好友排行榜")
majsoul_get_notify = SV("雀魂订阅推送")
majsoul_add_account = SV("雀魂账号池", pm=0)
majsoul_friend_manage = SV("雀魂好友管理", pm=0)


@majsoul_add_account.on_command(("添加账号"))
async def majsoul_add_at(bot: Bot, ev: Event):
    username, password = ev.text.strip()
    if not username or not password:
        return await bot.send("❌ 请输入有效的username和password!")

    connection = await manager.check_username_password(username, password)
    if isinstance(connection, bool):
        return await bot.send("❌ 登陆失败, 请输入正确的username和password!")

    friend_code = str(encode_account_id2(connection.account_id))
    if await MajsUser.data_exist(uid=connection.account_id):
        await MajsUser.update_data_by_data(
            {"uid": str(connection.account_id)},
            {
                "cookie": connection.access_token,
                "friend_code": friend_code,
                "username": username,
                "password": password,
            },
        )
    else:
        await MajsUser.insert_data(
            ev.user_id,
            ev.bot_id,
            uid=str(connection.account_id),
            cookie=connection.access_token,
            friend_code=friend_code,
            username=username,
            password=password,
        )

    conn = await manager.start()
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = "🥰成功向账号池添加账号！尝试自动连接中...\n"

    msg += f"当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}"
    await bot.send(msg)


@majsoul_get_notify.on_command(("订阅"))
async def majsoul_get_notify_command(bot: Bot, ev: Event):
    conn = await manager.start()
    if isinstance(conn, str):
        return await bot.send(conn)

    uid = await get_uid(bot, ev, MajsBind)
    if uid is None:
        return await bot.send(UID_HINT)

    logger.info(f"[majs] 开始订阅推送 {uid}, 进行刷新账号数据中...")
    await conn.fetchInfo()

    friend_code = await MajsUser.get_user_attr_by_uid(
        str(conn.account_id),
        "friend_code",
    )
    if friend_code is None:
        return await bot.send("[majs] 账号池账号异常, 请联系管理员!")

    for friend in conn.friends:
        if uid == str(friend.account_id):
            break
    else:
        return await bot.send(
            "[majs] 未找到好友信息! \n"
            f"请先在【游戏中】添加 {conn.nick_name}: {friend_code}好友再执行此操作！"
        )

    push_id = ev.group_id if ev.group_id else "on"
    if await MajsPush.data_exist(uid=uid):
        retcode = await MajsPush.update_data_by_uid(
            uid,
            ev.bot_id,
            push_id=push_id,
        )
    else:
        retcode = await MajsPush.full_insert_data(
            uid=uid,
            bot_id=ev.bot_id,
            user_id=ev.user_id,
            push_id=push_id,
        )

    if retcode == 0:
        logger.success(f"[majs] {uid}订阅推送成功！当前值：{push_id}")
        return await bot.send(f"[majs] 修改推送订阅成功！当前值：{push_id}")
    else:
        return await bot.send("[majs] 推送订阅失败！")


@majsoul_notify.on_fullmatch(("推送启动", "启动推送", "服务启动", "启动服务"))
async def majsoul_notify_command(bot: Bot, event: Event):
    conn = await manager.start()
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = "🥰成功启动雀魂订阅消息推送服务！\n"

    msg += f"当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}"
    await bot.send(msg)


@majsoul_notify.on_fullmatch(("重启订阅服务", "重启服务"))
async def majsoul_notify_reset_command(bot: Bot, event: Event):
    conn = await manager.restart()
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = "🥰成功重启雀魂订阅消息推送服务！\n"
    msg += f"当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}"
    await bot.send(msg)


@majsoul_notify.on_fullmatch(("检查服务", "检查订阅服务"))
async def majsoul_notify_check_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")

    if await conn.check_alive():
        msg = "雀魂服务连接正常！\n"
        msg += f"当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}"
    else:
        msg = "雀魂服务连接失败, 请先进行[雀魂重启订阅服务]"
    await bot.send(msg)


@majsoul_friend_level_billboard.on_command("好友排行榜")
async def majsoul_friend_billboard_command(bot: Bot, event: Event):
    # get connection
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")
    # get friends
    friends = conn.friends
    if "四" in event.text:
        # sort by level.id and level.score
        friends.sort(key=lambda x: (x.level.id, x.level.score), reverse=True)
        # get level info
        msg = "本群雀魂好友四麻排行榜\n"
        for friend in friends:
            level_str = friend.level.formatAdjustedScoreWithTag(friend.level.score)
            msg += f"{friend.nickname} {level_str}\n"
    else:
        friends.sort(key=lambda x: (x.level3.id, x.level3.score), reverse=True)
        msg = "本群雀魂好友三麻排行榜\n"
        for friend in friends:
            level_str = friend.level3.formatAdjustedScoreWithTag(friend.level3.score)
            msg += f"{friend.nickname} {level_str}\n"
    await bot.send(msg)


@majsoul_friend_manage.on_command("好友总览")
async def majsoul_friend_overview_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")
    friends = conn.friends
    msg = "本群雀魂好友列表\n"
    for friend in friends:
        msg += f"{friend.nickname} {friend.account_id}\n"
    await bot.send(msg)


@majsoul_friend_manage.on_command(("获取好友全部申请", "好友申请列表", "好友申请"))
async def majsoul_friend_apply_get_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")
    applys = conn.friend_apply_list
    msg = "本群雀魂好友申请列表\n"
    for apply in applys:
        msg += f"{apply}\n"
    await bot.send(msg)


@majsoul_friend_manage.on_command("同意所有好友申请")
async def majsoul_friend_apply_all_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")
    applys = conn.friend_apply_list
    for apply in applys:
        await conn.acceptFriendApply(apply)
    await bot.send("已同意所有好友申请")


@majsoul_friend_manage.on_command("同意好友申请")
async def majsoul_friend_apply_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")
    apply = int(event.text.strip())
    await conn.acceptFriendApply(apply)
    await bot.send("已同意好友申请")
