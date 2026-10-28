import random
import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx2
import email_validator

from gsuid_core.sv import SV
from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.server import on_core_start
from gsuid_core.segment import MessageSegment
from gsuid_core.subscribe import gs_subscribe
from gsuid_core.utils.database.api import get_uid
from gsuid_core.utils.database.models import Subscribe

from ..lib import lq as liblq
from .route import spawn
from .majsoul import (
    TASK_NAME_MAJSOUL_NOTIFY,
    MajsoulCredential,
    MajsoulLoginError,
    manager,
    get_paipu_by_game_id,
    yostar_passport_post,
)
from .constants import SERVER_NAME, SOCIO_TYPE_CN, SOCIO_TYPE_YOSTAR, ModeId2Room, yostar_region
from .draw_frame import render_frame
from .account_input import parse_account_password
from .tenhou.review import review_tenhou
from .draw_friend_rank import draw_friend_rank_img
from .draw_review_info import draw_review_info_img
from ..utils.api.remote import encode_account_id2
from ..utils.error_reply import UID_HINT
from ..utils.database.models import MajsBind, MajsPush, MajsUser
from ..utils.resource.RESOURCE_PATH import PAIPU_PATH

majsoul_notify = SV("雀魂推送服务", pm=0)
majsoul_add_account = SV("雀魂账号池", pm=0)
majsoul_friend_manage = SV("雀魂好友管理", pm=0)
majsoul_yostar_login = SV("雀魂Yostar登陆", pm=0)

majsoul_friend_level_billboard = SV("雀魂好友排行榜")
majsoul_get_notify = SV("雀魂订阅推送")
majsoul_friend_code = SV("雀魂好友码")
majsoul_review = SV("雀魂牌谱Review")

sv_majsoul_notify_sub = SV("雀魂推送订阅", pm=0)

# 观战模式存在 core 的 Subscribe 表（按用户一条），好友模式存在 MajsPush（按 UID），两套要一起管
TASK_NAME_MAJSOUL_WATCH = "雀魂观战订阅"
NO_CONN_HINT = "账号池没有在线的账号，请管理员先[雀魂推送启动]"
YOSTAR_AUTH_REQUEST_URL = "https://passport.mahjongsoul.com/account/auth_request"
YOSTAR_AUTH_SUBMIT_URL = "https://passport.mahjongsoul.com/account/auth_submit"


EXSAMPLE = """雀魂登陆国服 用户名, 密码
⚠ 提示: 该命令将会使用账密进行登陆, 请[永远]不要使用自己的大号, 否则可能会导致账号被封！
⚠ 请自行使用任何小号, 本插件不为账号被封禁承担任何责任！！
"""

EXSAMPLE_JP_EN = """雀魂登陆日服 邮箱
⚠ 提示: 该命令将会使用邮箱进行登陆, 请[永远]不要使用自己的大号, 否则可能会导致账号被封！
⚠ 请自行使用任何小号, 本插件不为账号被封禁承担任何责任！！
"""


@dataclass
class WatchedGame:
    game: liblq.GameLiveHead
    # 订阅（按 bot / 群 / 用户 / UID 区分）→ (订阅, 被观战的昵称)
    viewers: dict[tuple[str, str | None, str, str | None], tuple[Subscribe, str]]


cache_game: dict[str, WatchedGame] = {}
_background_tasks: set[asyncio.Task[None]] = set()


async def _auto_start_pool() -> None:
    for line in await manager.start():
        logger.info(f"[majs] {line}")


@on_core_start
async def start_majsoul_account_pool() -> None:
    # 和客户端打开时用存下的 token 自动登录一样（UI_Entrance:AutoLogin），放后台免得拖慢启动
    task = spawn(_auto_start_pool(), "majs-auto-start")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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

    tenhou_log = await get_paipu_by_game_id(desired_string)
    if tenhou_log is None:
        conn = manager.get_conn()
        if conn is None:
            return await bot.send(f"❌ {NO_CONN_HINT}")
        tenhou_log = await conn.fetchLogs(desired_string)
        if isinstance(tenhou_log, str):
            return await bot.send(f"❌ {tenhou_log}")
    if not tenhou_log:
        return await bot.send("❌ 未找到有效牌谱!")

    res = await review_tenhou(tenhou_log)
    if isinstance(res, str):
        return await bot.send(res)
    msg = []
    for i in range(len(res["data"]["review"]["kyokus"])):
        review_result = await draw_review_info_img(tenhou_log, res, i)
        msg.append(review_result)

    await bot.send(MessageSegment.node(msg))


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
    evt = ev.text.strip()
    try:
        email_v = email_validator.validate_email(evt)
        email = email_v.normalized  # type: ignore[reportAttributeAccessIssue]
    except email_validator.EmailNotValidError:
        return await bot.send("❌ 请输入有效的email!")

    lang = "ja" if "日" in ev.command else "en"
    region = yostar_region(lang)
    try:
        sent = await yostar_passport_post(region, YOSTAR_AUTH_REQUEST_URL, {"account": email, "lang": lang})
    except (MajsoulLoginError, httpx2.HTTPError) as e:
        return await bot.send(f"❌ 发送验证邮件失败: {e}")
    if sent.result != 0:
        logger.error(f"[majs] 发送验证邮件失败: {sent}")
        return await bot.send("❌ 发送验证邮件失败!")
    await bot.send("🥰 验证邮件已发送，请查收!")

    code = await bot.receive_resp("请输入验证码:")
    if code is None or not code.text.isdigit():
        return await bot.send("你输入了错误的格式!")

    try:
        submitted = await yostar_passport_post(region, YOSTAR_AUTH_SUBMIT_URL, {"account": email, "code": code.text})
    except (MajsoulLoginError, httpx2.HTTPError) as e:
        return await bot.send(f"❌ 登陆失败: {e}")
    if submitted.result != 0 or not submitted.token:
        logger.error(f"[majs] 验证码校验失败: {submitted}")
        return await bot.send("❌ 登陆失败!")
    uid = str(submitted.uid)
    token = submitted.token

    credential = MajsoulCredential(login_type=SOCIO_TYPE_YOSTAR, passport_uid=uid, passport_token=token, lang=lang)
    try:
        connection = await manager.add_account(credential)
    except MajsoulLoginError as e:
        return await bot.send(f"❌ 登陆失败: {e}")

    friend_code = str(encode_account_id2(connection.account_id))
    if await MajsUser.data_exist(uid=connection.account_id):
        await MajsUser.update_data_by_data(
            {"uid": str(connection.account_id)},
            {
                "cookie": connection.access_token,
                "friend_code": friend_code,
                "account": uid,
                "token": token,
                "lang": lang,
            },
        )
    else:
        await MajsUser.insert_data(
            ev.user_id,
            ev.bot_id,
            uid=str(connection.account_id),
            cookie=connection.access_token,
            friend_code=friend_code,
            account=uid,
            token=token,
            lang=lang,
            login_type=SOCIO_TYPE_YOSTAR,
        )

    msg = f"🥰成功向账号池添加{SERVER_NAME[lang]}账号！\n"
    msg += f"当前雀魂账号ID: {connection.account_id}, 昵称: {connection.nick_name}"
    await bot.send(msg)


@majsoul_add_account.on_command(("添加账号", "登陆国服", "登录国服"))
async def majsoul_add_at(bot: Bot, ev: Event):
    parsed = parse_account_password(ev.text)
    if parsed is None:
        return await bot.send(f"❌ 登陆失败!参考命令:\n{EXSAMPLE}")
    username, password = parsed

    credential = MajsoulCredential(login_type=SOCIO_TYPE_CN, username=username, password=password)
    try:
        connection = await manager.add_account(credential)
    except MajsoulLoginError as e:
        return await bot.send(f"❌ 登陆失败: {e}")

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

    msg = "🥰成功向账号池添加国服账号！\n"
    msg += f"当前雀魂账号ID: {connection.account_id}, 昵称: {connection.nick_name}"
    await bot.send(msg)


@majsoul_get_notify.on_command(("取消订阅"))
async def majsoul_cancel_notify_command(bot: Bot, ev: Event):
    uid = await get_uid(bot, ev, MajsBind)
    if uid is None:
        return await bot.send(UID_HINT)

    subscribes = await gs_subscribe.get_subscribe(TASK_NAME_MAJSOUL_WATCH)
    watching = [s for s in subscribes if s.user_id == ev.user_id] if subscribes else []
    push = await MajsPush.select_data_by_uid(uid)
    pushing = push is not None and push.push_id != "off"
    if not watching and not pushing:
        return await bot.send("[majs] 你现在没有开着的订阅")

    closed: list[str] = []
    if watching:
        await gs_subscribe.delete_subscribe("single", TASK_NAME_MAJSOUL_WATCH, ev)
        closed.append(f"观战模式（UID {'、'.join(str(s.extra_message) for s in watching)}）")
    if pushing:
        if await MajsPush.update_data_by_uid(uid, ev.bot_id, push_id="off") != 0:
            return await bot.send("[majs] 关闭好友模式推送失败，请稍后再试")
        closed.append(f"好友模式（UID {uid}）")
    logger.success(f"[majs] {ev.user_id} 关闭订阅: {closed}")
    return await bot.send(f"[majs] 已关闭订阅：{'，'.join(closed)}")


@majsoul_get_notify.on_command(("订阅"))
async def majsoul_get_notify_command(bot: Bot, ev: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send(f"[majs] {NO_CONN_HINT}")

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

    if int(uid) not in conn.friends:
        await bot.send(
            f"[majs] UID {uid} 还不是机器人好友，先用观战模式订阅。\n"
            "观战模式拿不到分数变化，推送可能不准。\n"
            f"想用好友模式：在【游戏中】加 {conn.nick_name}（好友码 {friend_code}）为好友，再发一次[雀魂订阅]"
        )

        await gs_subscribe.add_subscribe(
            "single",
            TASK_NAME_MAJSOUL_WATCH,
            ev,
            extra_message=uid,
        )
        return await bot.send("[观战模式] 订阅成功！关闭用[雀魂取消订阅]")

    # 换成好友模式后，旧的观战订阅还在会重复推送
    await gs_subscribe.delete_subscribe("single", TASK_NAME_MAJSOUL_WATCH, ev)
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
        where = "这个群" if ev.group_id else "私聊"
        return await bot.send(f"[majs] 好友模式订阅成功！UID {uid} 的对局结果会推送到{where}，关闭用[雀魂取消订阅]")
    else:
        return await bot.send("[majs] 推送订阅失败！")


@majsoul_friend_code.on_command(("好友码", "加好友"), block=True)
async def majsoul_friend_code_command(bot: Bot, ev: Event):
    # 绑定 / 查询用的是 UID（account_id），游戏里搜人要用好友码，两者不是一个数
    lines = [
        f"{SERVER_NAME[user.lang] if user.lang in SERVER_NAME else user.lang}：{encode_account_id2(int(user.uid))}"
        for user in await MajsUser.get_all_user()
        if user.uid is not None and user.uid.isdigit()
    ]
    if not lines:
        return await bot.send("账号池里还没有可用的机器人账号，请管理员先用[雀魂登陆国服]添加")
    await bot.send("机器人好友码：\n" + "\n".join(lines) + "\n通过好友申请后，用[雀魂订阅]就能推送分数变化")


@majsoul_notify.on_fullmatch(("推送启动", "启动推送", "服务启动", "启动服务"))
async def majsoul_notify_command(bot: Bot, event: Event):
    await bot.send("正在准备进行账号登陆中...可能需要一定时间!")
    lines = await manager.start()
    await bot.send("雀魂账号池：\n" + "\n".join(lines))


@majsoul_notify.on_fullmatch(("重启订阅服务", "重启服务"))
async def majsoul_notify_reset_command(bot: Bot, event: Event):
    lines = await manager.restart()
    await bot.send("已重新登录雀魂账号池：\n" + "\n".join(lines))


@majsoul_notify.on_fullmatch(("检查服务", "检查订阅服务"))
async def majsoul_notify_check_command(bot: Bot, event: Event):
    conns = manager.get_all_conn()
    if not conns:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")
    msg = "\n".join(conn.status_line() for conn in conns)
    if any(conn.is_closed() for conn in conns):
        msg += "\n有账号不在线，可以发送[雀魂重启服务]重新登录"
    await bot.send(msg)


@majsoul_friend_level_billboard.on_command("好友排行榜")
async def majsoul_friend_billboard_command(bot: Bot, event: Event):
    # 私聊没有「本群」，放开就能看到所有群的人
    if not event.group_id:
        return await bot.send("好友排行榜只能在群里看，只列出本群绑定了 UID 的人")

    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")

    uids = await MajsBind.get_group_uids(event.bot_id, event.real_bot_id, event.group_id)
    friends = [friend for friend in conn.friends.values() if str(friend.account_id) in uids]
    if not uids:
        return await bot.send("本群还没有人绑定 UID，先用[雀魂绑定uid]绑定")
    if not friends:
        return await bot.send(
            f"本群绑定的 {len(uids)} 个 UID 都还不是机器人好友。用[雀魂好友码]查到好友码，在游戏里加上就能上榜"
        )

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
    # 这是账号池的全部好友，不分群；按群看用[雀魂好友排行榜]
    msg = f"账号池好友列表（{len(conn.friends)} 人）\n"
    for friend in conn.friends.values():
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
    # acceptFriendApply 会从原列表里删，直接遍历会隔一个跳一个
    for apply in list(conn.friend_apply_list):
        await conn.acceptFriendApply(apply)
    await bot.send("已同意所有好友申请")


@majsoul_friend_manage.on_command("同意好友申请")
async def majsoul_friend_apply_command(bot: Bot, event: Event):
    conn = manager.get_conn()
    if conn is None:
        return await bot.send("未找到有效连接, 请先进行[雀魂推送启动]")
    text = event.text.strip()
    if not text.isdigit():
        return await bot.send("请带上对方的雀魂 UID，例如：雀魂同意好友申请 12345678")
    await conn.acceptFriendApply(int(text))
    await bot.send("已同意好友申请")


def watch_key(subscribe: Subscribe) -> tuple[str, str | None, str, str | None]:
    return subscribe.bot_id, subscribe.group_id, subscribe.user_id, subscribe.extra_message


def room_name(game: liblq.GameLiveHead) -> str:
    mode_id = game.game_config.meta.mode_id
    return ModeId2Room[mode_id] if mode_id in ModeId2Room else ""


@scheduler.scheduled_job("cron", minute="*/2")
async def majsoul_notify_rank():
    await asyncio.sleep(random.randint(0, 1))
    datas = await gs_subscribe.get_subscribe(TASK_NAME_MAJSOUL_WATCH)

    if not datas:
        return

    # 只用在线的连接，账号池掉线时不在这里触发登录
    conn = manager.get_conn()
    if conn is None:
        logger.debug("[majs] 账号池没有在线的账号，跳过观战检查")
        return

    live_games = {game.uuid: game for game in await conn.fetchLiveGames()}

    for uuid, live_game in live_games.items():
        for subscribe in datas:
            key = watch_key(subscribe)
            if uuid in cache_game and key in cache_game[uuid].viewers:
                continue
            for player in live_game.players:
                if str(subscribe.extra_message) != str(player.account_id):
                    continue
                await subscribe.send(
                    f"[订阅] {player.nickname} 正在进行 {room_name(live_game)} 的对局!\n对局ID: {uuid}"
                )
                if uuid not in cache_game:
                    cache_game[uuid] = WatchedGame(game=live_game, viewers={})
                cache_game[uuid].viewers[key] = (subscribe, player.nickname)
                break

    for uuid in [uuid for uuid in cache_game if uuid not in live_games]:
        watched = cache_game.pop(uuid)
        for subscribe, nickname in watched.viewers.values():
            await subscribe.send(f"[订阅] {nickname} 结束了 {room_name(watched.game)} 的对局!\n对局ID: {uuid}")


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
