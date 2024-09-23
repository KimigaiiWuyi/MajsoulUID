from gsuid_core.bot import Bot
from gsuid_core.models import Event
from gsuid_core.sv import SV

from .majsoul import manager

majsoul_notify = SV("雀魂好友信息推送")
majsoul_notify_reset = SV("重置雀魂好友信息推送")
majsoul_friend_level_billboard = SV("雀魂好友四麻排行榜")
majsoul_friend_level3_billboard = SV("雀魂好友三麻排行榜")
majsoul_notify_check = SV("检查雀魂好友信息推送")

IN_NOTIFY = []


@majsoul_notify.on_command(("MajsoulNotify", "StartMajsoulNotify"))
async def majsoul_notify_command(bot: Bot, event: Event):
    group_id = event.group_id
    conn = await manager.start()
    msg = "Successfullly started majsoul notify\n"
    if group_id in IN_NOTIFY:
        await bot.send("Connection is already started")
        return
    IN_NOTIFY.append(group_id)
    msg += f"Current majsoul accountId: {conn.account_id}, nickName: {conn.nick_name}"
    await bot.send(msg)


@majsoul_notify_reset.on_command("RestartMajsoulNotify")
async def majsoul_notify_reset_command(bot: Bot, event: Event):
    group_id = event.group_id
    conn = await manager.restart()
    msg = "Successfullly restart majsoul notify"
    if group_id not in IN_NOTIFY:
        IN_NOTIFY.append(group_id)
    msg += f"Current majsoul accountId: {conn.account_id}, nickName: {conn.nick_name}"
    await bot.send(msg)


@majsoul_notify_check.on_command("CheckMajsoulNotify")
async def majsoul_notify_check_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        await bot.send("Connection is None, please start majsoul notify first")
        return
    if await conn.check_alive():
        msg = "Connection is alive\n"
        msg += f"Current majsoul accountId: {conn.account_id}, nickName: {conn.nick_name}"
    else:
        msg = "Connection is dead. Please send RestartMajsoulNotify to restart"
    await bot.send(msg)


@majsoul_friend_level_billboard.on_command("MajsoulFriendLevelBillboard")
async def majsoul_friend_billboard_command(bot: Bot, event: Event):
    # get connection
    conn = manager.get_conn()
    if conn is None:
        await bot.send("Connection is None, please start majsoul notify first")
        return
    # get friends
    friends = conn.friends
    # sort by level.id and level.score
    friends.sort(key=lambda x: (x.level.id, x.level.score), reverse=True)
    # get level info
    msg = "本群雀魂好友四麻排行榜\n"
    for friend in friends:
        level_str = friend.level.formatAdjustedScoreWithTag()
        msg += f"{friend.nickname} {level_str}\n"
    await bot.send(msg)


@majsoul_friend_level3_billboard.on_command("MajsoulFriendLevel3Billboard")
async def majsoul_friend_level3_billboard_command(bot: Bot, event: Event):
    # get connection
    conn = manager.get_conn()
    if conn is None:
        await bot.send("Connection is None, please start majsoul notify first")
        return
    # get friends
    friends = conn.friends
    # sort by level.id and level.score
    friends.sort(key=lambda x: (x.level3.id, x.level3.score), reverse=True)
    # get level info
    msg = "本群雀魂好友三麻排行榜\n"
    for friend in friends:
        level_str = friend.level3.formatAdjustedScoreWithTag()
        msg += f"{friend.nickname} {level_str}\n"
    await bot.send(msg)
