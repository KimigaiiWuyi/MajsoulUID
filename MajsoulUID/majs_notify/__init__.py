import asyncio
import random
from urllib.parse import parse_qs, urlparse

import email_validator
import httpx
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.sv import SV
from gsuid_core.utils.database.api import get_uid

from ..utils.api.remote import encode_account_id2
from ..utils.database.models import MajsBind, MajsPush, MajsUser
from ..utils.error_reply import UID_HINT
from ..utils.resource.RESOURCE_PATH import PAIPU_PATH
from .constants import USER_AGENT, ModeId2Room
from .draw_frame import render_frame
from .draw_friend_rank import draw_friend_rank_img
from .draw_review_info import draw_review_info_img
from .majsoul import (
    TASK_NAME_MAJSOUL_NOTIFY,
    MajsoulMaintenanceError,
    get_paipu_by_game_id,
    manager,
)
from .tenhou.review import get_review_result, review_tenhou

majsoul_notify = SV("雀魂推送服务", pm=0)
majsoul_add_account = SV("雀魂账号池", pm=0)
majsoul_friend_manage = SV("雀魂好友管理", pm=0)
majsoul_yostar_login = SV("雀魂Yostar登陆", pm=0)

majsoul_friend_level_billboard = SV("雀魂好友排行榜")
majsoul_get_notify = SV("雀魂订阅推送")
majsoul_review = SV("雀魂牌谱Review")

sv_majsoul_notify_sub = SV("雀魂推送订阅", pm=0)


EXSAMPLE = """雀魂登陆国服 用户名, 密码
⚠ 提示: 该命令将会使用账密进行登陆, 请[永远]不要使用自己的大号, 否则可能会导致账号被封！
⚠ 请自行使用任何小号, 本插件不为账号被封禁承担任何责任！！
"""

EXSAMPLE_JP_EN = """雀魂登陆日服 邮箱
⚠ 提示: 该命令将会使用邮箱进行登陆, 请[永远]不要使用自己的大号, 否则可能会导致账号被封！
⚠ 请自行使用任何小号, 本插件不为账号被封禁承担任何责任！！
"""

cache_game = {}


@majsoul_review.on_command(("牌谱Review", "牌谱review", "Review", "review"))
async def majsoul_review_command(bot: Bot, ev: Event):
    paipu_url = ev.text.strip()
    parsed_url = urlparse(paipu_url)

    query_params = parse_qs(parsed_url.query)

    paipu_value = query_params.get("paipu")
    if paipu_value:
        desired_string = paipu_value[0]
    else:
        return await bot.send("❌ 请输入有效的牌谱URL!")

    path1 = PAIPU_PATH / f"{desired_string} - raw.json"
    path2 = PAIPU_PATH / f"{desired_string} - review.json"

    if not (path1.exists() and path2.exists()):
        conns = manager.get_all_conn()
        if not conns:
            return await bot.send("❌ 未找到有效连接, 请先进行[雀魂推送启动]")
        conn = random.choice(conns)
        tenhou_log = await conn.fetchLogs(desired_string)
    else:
        tenhou_log = await get_paipu_by_game_id(desired_string)

    if not tenhou_log:
        return await bot.send("❌ 未找到有效牌谱!")

    res = await review_tenhou(tenhou_log)
    if isinstance(res, str):
        return await bot.send(res)
    if False:
        review_result = await get_review_result(res)
    else:
        for i in range(len(res["data"]["review"]["kyokus"])):
            review_result = await draw_review_info_img(tenhou_log, res, i)
            await bot.send(review_result)


@majsoul_review.on_command(("场况", "牌谱详情"))
async def majsoul_render_log(bot: Bot, ev: Event):
    et = ev.text.strip().replace("，", ",").replace(",", " ")
    paipu_command = et.split(" ")
    if len(paipu_command) != 3:
        return await bot.send("❌ 请输入有效的格式!\n例如：雀魂场况 241118 10 5")

    paipu_id = paipu_command[0]
    kyoku_id = int(paipu_command[1])
    meguru_id = int(paipu_command[2])

    for path in PAIPU_PATH.iterdir():
        if path.name.startswith(paipu_id) and path.name.endswith("- raw.json"):
            paipu_id = path.stem[:-6].strip()
            paipu = await get_paipu_by_game_id(paipu_id)
            break
    else:
        return await bot.send("❌ 未找到有效牌谱!\n请先使用[雀魂牌谱review + URL]")

    if paipu is None:
        return await bot.send("❌ 未找到有效牌谱!\n请先使用[雀魂牌谱review + URL]")

    res = await review_tenhou(paipu)
    if isinstance(res, str):
        return await bot.send(res)

    im = await render_frame(res, paipu, kyoku_id, meguru_id)
    await bot.send(im)


@majsoul_yostar_login.on_command(("登录美服", "登录日服", "登陆日服", "登陆美服"))
async def majsoul_jp_login_command(bot: Bot, ev: Event):
    url = "https://passport.mahjongsoul.com/account/auth_request"
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        "Referer": "https://game.mahjongsoul.com/",
        "Origin": "https://game.mahjongsoul.com",
    }
    evt = ev.text.strip()
    try:
        email_v = email_validator.validate_email(evt)
        email = email_v.normalized  # type: ignore[reportAttributeAccessIssue]
    except email_validator.EmailNotValidError:
        return await bot.send("❌ 请输入有效的email!")

    lang = "ja" if "日" in ev.command else "en"
    sess = httpx.AsyncClient(headers=headers, verify=False)
    payload = {"account": email, "lang": lang}
    response = await sess.post(url, json=payload)
    if response.status_code == 200:
        res = response.json()
        if res["result"] == 0:
            await bot.send("🥰 验证邮件已发送，请查收!")
        else:
            logger.error(res)
            return await bot.send("❌ 发送验证邮件失败!")
    else:
        logger.error(response.text)
        return await bot.send("❌ 发送验证邮件失败!")
    code = await bot.receive_resp("请输入验证码:")
    if code is None or not code.text.isdigit():
        return await bot.send("你输入了错误的格式!")

    url = "https://passport.mahjongsoul.com/account/auth_submit"
    payload = {"account": email, "code": code.text}
    response = await sess.post(url, json=payload)
    if response.status_code == 200:
        res = response.json()
        if res["result"] == 0:
            uid = res["uid"]
            token = res["token"]
        else:
            logger.error(res)
            return await bot.send("❌ 登陆失败!")
    else:
        logger.error(response.text)
        return await bot.send("❌ 登陆失败!")

    url = "https://passport.mahjongsoul.com/user/login"
    payload = {"uid": uid, "token": token, "deviceId": f"web|{uid}"}
    response = await sess.post(url, json=payload)
    if response.status_code == 200:
        res = response.json()
        if res["result"] == 0:
            code = res["accessToken"]
        else:
            logger.error(res)
            return await bot.send("❌ 登陆失败!")
    else:
        logger.error(response.text)
        return await bot.send("❌ 登陆失败!")

    try:
        connection = await manager.check_yostar_login(
            uid,
            code,
            lang,
        )
    except ConnectionRefusedError:
        return await bot.send("❌ 登陆失败, 可能是网络原因, 请检查控制台!")

    if isinstance(connection, str):
        return await bot.send(connection)

    if isinstance(connection, bool):
        return await bot.send("❌ 登陆失败, 请检查登录信息!")

    friend_code = str(encode_account_id2(connection.account_id))
    if await MajsUser.data_exist(uid=connection.account_id):
        await MajsUser.update_data_by_data(
            {"uid": str(connection.account_id)},
            {
                "cookie": connection.access_token,
                "friend_code": friend_code,
                "token": token,
            },
        )
    else:
        await MajsUser.insert_data(
            ev.user_id,
            ev.bot_id,
            uid=str(connection.account_id),
            cookie=connection.access_token,
            friend_code=friend_code,
            token=token,
            lang=lang,
            login_type=7,
        )

    msg = f"🥰成功向账号池添加{lang}账号！\n"
    msg += f"当前雀魂账号ID: {connection.account_id}, 昵称: {connection.nick_name}"
    await bot.send(msg)


@majsoul_add_account.on_command(("添加账号", "登陆国服", "登录国服"))
async def majsoul_add_at(bot: Bot, ev: Event):
    evt = ev.text.strip()
    if not evt:
        return await bot.send(f"❌ 登陆失败!参考命令:\n{EXSAMPLE}")

    evt = evt.replace(",", " ").replace("，", " ")

    if " " in evt:
        username, password = evt.split(" ")
        if not username or not password:
            return await bot.send("❌ 请输入有效的username和password!")

        connection = await manager.check_username_password(
            username,
            password,
            "",
            0,
        )
        if isinstance(connection, str):
            return await bot.send(connection)
        if isinstance(connection, bool):
            return await bot.send("❌ 登陆失败, 请输入正确的username和password!")
    else:
        return await bot.send(f"❌ 登陆失败!参考命令:\n{EXSAMPLE}")

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
    if isinstance(conn, MajsoulMaintenanceError):
        msg = f"❌ 登陆失败, 雀魂服务器正在维护中!\ncontext: {conn}"
        return await bot.send(msg)
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = "🥰成功向账号池添加国服账号！尝试自动连接中...\n"

    msg += f"当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}"
    await bot.send(msg)


@majsoul_get_notify.on_command(("取消订阅"))
async def majsoul_cancel_notify_command(bot: Bot, ev: Event):
    uid = await get_uid(bot, ev, MajsBind)
    if uid is None:
        return await bot.send(UID_HINT)

    if await MajsPush.data_exist(uid=uid):
        data = await MajsPush.select_data_by_uid(uid)
        if data and data.push_id == "off":
            return await bot.send("[majs] 你已经关闭了订阅信息!")
        elif data is None:
            return await bot.send("[majs] 你尚未有订阅信息, 无法取消!")
        else:
            push_id = "off"
            retcode = await MajsPush.update_data_by_uid(
                uid,
                ev.bot_id,
                push_id="off",
            )
            if retcode == 0:
                logger.success(f"[majs] {uid}订阅推送成功！当前值：{push_id}")
                return await bot.send(f"[majs] 修改推送订阅成功！当前值：{push_id}")
            else:
                return await bot.send("[majs] 推送订阅失败！")
    else:
        return await bot.send("[majs] 你尚未有订阅信息, 无法取消!")


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
        await bot.send(
            "[majs] 未找到好友信息! 将使用观战订阅模式！\n"
            "该模式无法获取好友分数变化情况, 可能存在不准确的情况, 请自行关注！\n"
            "如需使用好友订阅模式,"
            f"请先在【游戏中】添加 {conn.nick_name}: {friend_code}好友再执行此操作！"
        )

        await gs_subscribe.add_subscribe(
            "single",
            "雀魂观战订阅",
            ev,
            extra_message=uid,
        )
        return await bot.send("[观战模式] 订阅成功！")

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
    await bot.send("正在准备进行账号登陆中...可能需要一定时间!")
    conn = await manager.start()
    if isinstance(conn, str):
        return await bot.send(conn)

    msg = "🥰 成功启动雀魂订阅消息推送服务！\n"

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
    conns = manager.get_all_conn()
    if not conns:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")

    msg_list = []
    for conn in conns:
        msg_list = []
        if await conn.check_alive():
            a = f"✅ 当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name}"
        else:
            a = f"❌ 当前雀魂账号ID: {conn.account_id}, 昵称: {conn.nick_name} 账号登录态失效!"
            a += "请使用[雀魂重启订阅服务]"

        msg_list.append(a)

    msg = "\n".join(msg_list)
    await bot.send(msg)


@majsoul_friend_level_billboard.on_command("好友排行榜")
async def majsoul_friend_billboard_command(bot: Bot, event: Event):
    # get connection
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")

    # 去重
    friends = conn.remove_duplicate_friends()
    if "三" in event.text:
        friends.sort(key=lambda x: (x.level3.id, x.level3_score), reverse=True)
        msg = await draw_friend_rank_img(friends, "3")
    else:
        friends.sort(key=lambda x: (x.level.id, x.level_score), reverse=True)
        msg = await draw_friend_rank_img(friends, "4")
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


@majsoul_friend_manage.on_command(("获取好友全部申请", "好友申请"))
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


@scheduler.scheduled_job("cron", minute="*/2")
async def majsoul_notify_rank():
    await asyncio.sleep(random.randint(0, 1))
    datas = await gs_subscribe.get_subscribe("雀魂观战订阅")

    if not datas:
        return

    conn = await manager.start()
    if isinstance(conn, str):
        return logger.error(conn)

    live_games = await conn.fetchLiveGames()

    _cache_game = []
    for subscribe in datas:
        for live_game in live_games:
            _cache_game.append(live_game.uuid)
            if live_game.uuid in cache_game:
                continue

            for player in live_game.players:
                if str(subscribe.extra_message) == str(player.account_id):
                    mode_id = live_game.game_config.meta.mode_id
                    room_name = ModeId2Room.get(mode_id, "")
                    nickname = player.nickname
                    _id = f"对局ID: {live_game.uuid}"
                    msg = f"[订阅] {nickname} 正在进行 {room_name} 的对局!\n{_id}"
                    await subscribe.send(msg)
                    cache_game[live_game.uuid] = {
                        "game": live_game,
                        "subscribe": subscribe,
                        "nickname": nickname,
                    }

    del_game = []
    for key in cache_game:
        if key not in _cache_game:
            game = cache_game[key]["game"]
            subscribe = cache_game[key]["subscribe"]
            mode_id = game.game_config.meta.mode_id
            room_name = ModeId2Room.get(mode_id, "")
            nickname = cache_game[key]["nickname"]
            _id = f"对局ID: {key}"
            msg = f"[订阅] {nickname} 结束了 {room_name} 的对局!\n{_id}"
            del_game.append(key)

    for key in del_game:
        del cache_game[key]


@sv_majsoul_notify_sub.on_fullmatch("订阅雀魂推送")
async def subscribe_majsoul_notify(bot: Bot, ev: Event):
    """订阅雀魂推送"""
    if ev.group_id is None:
        return await bot.send("请在群聊中订阅")

    try:
        data = await gs_subscribe.get_subscribe(TASK_NAME_MAJSOUL_NOTIFY)
        if data:
            for subscribe in data:
                if subscribe.group_id == ev.group_id:
                    return await bot.send("已经订阅了雀魂推送！")

        await gs_subscribe.add_subscribe(
            "session",
            task_name=TASK_NAME_MAJSOUL_NOTIFY,
            event=ev,
            extra_message="",
        )

        logger.info(f"新增雀魂推送订阅: {ev.group_id}")
        await bot.send("成功订阅雀魂推送!")

    except Exception as e:
        logger.error(f"订阅雀魂推送失败: {e}")
        await bot.send("订阅失败，请稍后再试")
