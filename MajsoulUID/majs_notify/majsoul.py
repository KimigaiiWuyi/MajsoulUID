import hmac
import json
import time
import uuid
import random
import asyncio
import hashlib
import contextlib
from typing import Dict, List, Union, Sequence, cast

import httpx2
import msgspec
import aiofiles
import websockets.client
import websockets.exceptions

from gsuid_core.gss import gss
from gsuid_core.logger import logger
from gsuid_core.subscribe import gs_subscribe

from ..lib import lq as liblq
from .codec import MajsoulProtoCodec
from .model import (
    MjsLog,
    MajsoulRpc,
    MjsLogItem,
    MajsoulRoute,
    MajsoulRoutesResp,
    MajsoulLoginFields,
    MajsoulClientVersion,
    MajsoulDecodedMessage,
)
from ._level import MajsoulLevel
from .constants import (
    REGION_CN,
    USER_AGENT,
    CLIENT_PLATFORM,
    DETAIL_PLATFORM,
    ROUTE_TYPE_MAIN,
    DETAIL_PLATFORM_ID,
    LOGIN_BEAT_CONTRACT,
    ModeId2Room,
    MajsoulRegion,
    yostar_region,
)
from ..utils.map import PROTO_PATH
from .tenhou.parser import MajsoulPaipuParser
from .majsoul_friend import MajsoulFriend
from ..utils.api.remote import (
    decode_log_id,
    encode_account_id,
    decode_account_id2,
)
from ..utils.database.models import MajsPush, MajsUser, MajsPaipu
from ..utils.api.remote_const import GameMode
from ..majs_config.majs_config import MAJS_CONFIG
from ..utils.resource.RESOURCE_PATH import PAIPU_PATH

PP_HOST = "https://game.maj-soul.com/1/?paipu="
TASK_NAME_MAJSOUL_NOTIFY = "订阅雀魂消息推送"
# 第一次立刻查，之后逐次拉长间隔，总共等一分多钟
GAME_RECORD_RETRY_DELAYS = (0, 3, 5, 10, 20, 30)

# 两份文件都由 utils/proto/update_proto.py 从 MajsoulData 生成
RPC_MAP = msgspec.json.decode((PROTO_PATH / "liqi.json").read_bytes(), type=dict[str, MajsoulRpc])
CLIENT_VERSION = msgspec.json.decode((PROTO_PATH / "client_version.json").read_bytes(), type=MajsoulClientVersion)


class MajsoulMaintenanceError(Exception):
    pass


def make_device_id(region: MajsoulRegion, identity: str) -> str:
    # 真实客户端的 device_id 存在本地、换次登录也不变，这里按账号固定一个
    if not identity:
        return str(uuid.uuid4())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"majsoul:{region.tag}:{identity}"))


def process_dict(obj):
    if isinstance(obj, dict):
        return {key: process_dict(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [process_dict(item) for item in obj]
    elif hasattr(obj, "__dict__"):
        return process_dict(obj.__dict__)
    elif isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    elif isinstance(obj, bytes):
        return str(obj)
    else:
        logger.warning(f"Unsupported type: {type(obj)}")
        return str(obj)


def describe_game(game: liblq.AccountPlayingGame) -> tuple[int, str, str]:
    """返回 (牌谱类型, 类型名, 推送里显示的场次名)。"""
    category = game.category
    if category == 1:
        type_name = "歹人场"
    elif category == 2:
        type_name = "段位场"
    elif category == 4:
        type_name = "比赛场"
    else:
        type_name = "未知牌谱类型"
    mode_id = game.meta.mode_id
    room_name = ModeId2Room[mode_id] if mode_id in ModeId2Room else ""
    return category, type_name, room_name or type_name


async def save_paipu(account_id: int, game_uuid: str, category: int, type_name: str) -> None:
    if not await MajsPaipu.data_exist(uuid=game_uuid):
        await MajsPaipu.insert_data(
            account_id=str(account_id),
            uuid=game_uuid,
            paipu_type=category,
            paipu_type_name=type_name,
        )


async def get_paipu_by_game_id(game_id: str) -> Union[Dict, None]:
    path = PAIPU_PATH / f"{game_id} - raw.json"
    if path.exists():
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            data = json.loads(await f.read())
            return data


class MajsoulConnection:
    def __init__(
        self,
        route: MajsoulRoute,
        region: MajsoulRegion,
        login_type: int,
        codec: MajsoulProtoCodec,
        device_id: str,
    ):
        self._route = route
        self._endpoint = f"{'wss' if route.ssl else 'ws'}://{route.domain}/gateway"
        self._region = region
        self._codec = codec
        self._ws = None
        self._req_events: dict[int, asyncio.Event] = {}
        self._res: dict[int, MajsoulDecodedMessage] = {}
        self.clientVersionString = f"{CLIENT_PLATFORM}_2022-{CLIENT_VERSION.resource_version}"
        self.no_operation_counter = 0
        self.bg_tasks = []
        self.queue = asyncio.queues.Queue()
        self.account_id = 0
        self.nick_name = ""
        # 增删一律换新 dict：好友状态推送会在 await 中途遍历它
        self.friends: dict[int, MajsoulFriend] = {}
        self.friend_apply_list: list[int] = []
        # 好友 id → (等待中的上下线推送, 开始抖动前的在线状态)
        self._online_pending: dict[int, tuple[asyncio.Task[None], bool]] = {}
        self.random_key = device_id
        self.login_type = login_type

        self.manual_login_username = ""
        self.manual_login_password = ""
        self.access_token = ""

    def login_fields(self) -> MajsoulLoginFields:
        return {
            "device": {
                "platform": "pc",
                "hardware": "unknown",
                "os": "windows",
                "os_version": "Windows 10 64bit",
                "is_browser": True,
                "sale_platform": "web",
                "user_agent": USER_AGENT,
                "screen_width": 1920,
                "screen_height": 1080,
                "screen_type": 1,
                "device_id": self.random_key,
            },
            "random_key": self.random_key,
            "client_version": {
                "resource": CLIENT_VERSION.resource_version,
                "package": CLIENT_VERSION.package_version,
            },
            "currency_platforms": list(self._region.currency_platforms),
            "client_version_string": self.clientVersionString,
            "tag": self._region.tag,
        }

    async def check_alive(self):
        if self._ws is None:
            return False
        resp = cast(
            liblq.ResCommon,
            await self.rpc_call(
                ".lq.Route.heartbeat",
                {
                    "delay": random.randint(0, 200),
                    "no_operation_counter": 0,
                    "platform": 11,
                    "network_quality": random.randint(0, 100),
                },
            ),
        )
        if resp.error.code:
            return False
        return True

    async def start_sv(self):
        await asyncio.gather(self.process(), self.dispatch_msg())

    async def connect(self):
        logger.info(f"Connecting to {self._endpoint}")
        self._ws = await websockets.client.connect(  # type: ignore
            self._endpoint,
            ping_interval=30,
            ping_timeout=20,
        )
        self._msg_dispatcher = asyncio.create_task(self.start_sv())

    async def request_connection(self) -> None:
        # Unity 客户端连上路由后先发这一句，result 不为 1 说明这条路由拒绝了本次连接
        resp = await self.rpc_call(
            ".lq.Route.requestConnection",
            {
                "type": ROUTE_TYPE_MAIN,
                "route_id": self._route.id,
                "timestamp": int(time.time() * 1000),
                "platform": DETAIL_PLATFORM,
            },
        )
        if not isinstance(resp, liblq.ResRequestConnection) or resp.error.code or resp.result != 1:
            raise ConnectionError(f"[majs] 路由 {self._route.id} 拒绝连接: {resp}")

    def is_closed(self) -> bool:
        return self._ws is None or getattr(self._ws, "closed", True)

    def _wake_pending_requests(self) -> None:
        for evt in self._req_events.values():
            evt.set()

    async def _notify_subscribers(self, task_name: str, message: str):
        """通知订阅者"""
        try:
            subscribers = await gs_subscribe.get_subscribe(task_name)
            if not subscribers:
                logger.info(f"[{task_name}] 暂无群订阅")
                return

            for subscribe in subscribers:
                try:
                    await subscribe.send(message)
                    await asyncio.sleep(random.uniform(1, 3))
                except Exception as e:
                    logger.error(f"发送通知失败: {subscribe.group_id}, 错误: {e}")

        except Exception as e:
            logger.error(f"获取订阅列表失败: {e}")

    async def send_meta(self, meta_msg):
        # meta_bot_id = MAJS_CONFIG.get_config("MajsFriendPushBotId").data
        # meta_type = MAJS_CONFIG.get_config("MajsFriendPushType").data
        # meta_id: str = MAJS_CONFIG.get_config("MajsFriendPushID").data

        # if meta_id:
        #     for BOT_ID in gss.active_bot:
        #         bot = gss.active_bot[BOT_ID]
        #         await bot.target_send(
        #             meta_msg,
        #             meta_type,
        #             meta_id,
        #             meta_bot_id,
        #             "",
        #         )
        # else:
        #     logger.warning("[majs] 未配置元数据推送对象, 请前往网页控制台配置推送对象!")
        await self._notify_subscribers(TASK_NAME_MAJSOUL_NOTIFY, meta_msg)

    async def send_msg_to_user(self, target_user: str, msg):
        if MAJS_CONFIG.get_config("MajsIsPushActiveToMaster").data:
            await self.send_meta(msg)

        push_data = await MajsPush.select_data_by_uid(uid=str(target_user))
        if push_data:
            if push_data.push_id != "off":
                bot_id = push_data.bot_id
                if push_data.push_id == "on":
                    push_target = push_data.user_id
                    push_type = "direct"
                else:
                    push_target = push_data.push_id
                    push_type = "group"

                for BOT_ID in gss.active_bot:
                    bot = gss.active_bot[BOT_ID]
                    await bot.target_send(
                        msg,
                        push_type,
                        push_target,
                        bot_id,
                        "",
                    )

    async def handle_notify(self, notify: MajsoulDecodedMessage):
        logger.info(f"[majs] 通知: {notify}")
        if notify.method_name == ".lq.NotifyFriendStateChange":
            self.queue.put_nowait(self.handle_FriendStateChange(notify))
        elif notify.method_name == ".lq.NotifyFriendViewChange":
            self.queue.put_nowait(self.handle_FriendViewChange(notify))
        elif notify.method_name == ".lq.NotifyNewFriendApply":
            self.queue.put_nowait(self.handle_NewFriendApply(notify))
        elif notify.method_name == ".lq.NotifyFriendChange":
            self.queue.put_nowait(self.handle_FriendChange(notify))
        elif notify.method_name == ".lq.NotifyAnotherLogin":
            self.queue.put_nowait(self.handle_AnotherLogin())
        else:
            logger.warning(f"[majs] 未知通知: {notify}")

    async def handle_AnotherLogin(self):
        meta_msg = f"账号 {self.nick_name}({self.account_id}) 在别处登陆\n"
        meta_msg += "请检查AccessToken, 可能已过期！"
        await self.send_meta(meta_msg)

    async def handle_FriendStateChange(self, notify: MajsoulDecodedMessage):
        data = notify.payload
        if not isinstance(data, liblq.NotifyFriendStateChange) or data.target_id not in self.friends:
            return
        friend = self.friends[data.target_id]
        state = data.active_state
        was_online = friend.is_online
        old_game = friend.playing
        # 先落状态：拉牌谱要等几十秒，期间到达的推送得拿新状态去比
        friend.change_state(state)

        if state.is_online != was_online:
            self.schedule_online_push(friend.account_id, was_online)
        new_uuid = state.playing.game_uuid
        if old_game.game_uuid and old_game.game_uuid != new_uuid:
            await self.on_game_end(friend, old_game)
        if new_uuid and new_uuid != old_game.game_uuid:
            await self.on_game_start(friend, state.playing)

    def schedule_online_push(self, account_id: int, was_online: bool) -> None:
        if not MAJS_CONFIG.get_config("MajsPushOnlineState").data:
            return
        if account_id in self._online_pending:
            # 还在抖动：取消上一次等待，基准仍是第一次变化前的状态
            task, was_online = self._online_pending[account_id]
            task.cancel()
        task = asyncio.create_task(self.push_online_state(account_id, was_online))
        self._online_pending[account_id] = (task, was_online)

    async def push_online_state(self, account_id: int, was_online: bool) -> None:
        # 掉线重连会在几秒内连发下线、上线，状态稳定一段时间后再推
        await asyncio.sleep(MAJS_CONFIG.get_config("MajsOnlineStateDelay").data)
        del self._online_pending[account_id]
        if account_id not in self.friends or self.friends[account_id].is_online == was_online:
            return
        friend = self.friends[account_id]
        msg = f"{friend.nickname} 上线了" if friend.is_online else f"{friend.nickname} 下线了"
        await self.send_msg_to_user(str(account_id), msg)

    async def on_game_start(self, friend: MajsoulFriend, game: liblq.AccountPlayingGame) -> None:
        category, type_name, place = describe_game(game)
        await save_paipu(friend.account_id, game.game_uuid, category, type_name)
        if MAJS_CONFIG.get_config("MajsPushGameStart").data:
            await self.send_msg_to_user(str(friend.account_id), f"{friend.nickname} 开始了在 {place} 的对局")

    async def on_game_end(self, friend: MajsoulFriend, game: liblq.AccountPlayingGame) -> None:
        category, type_name, place = describe_game(game)
        account_id = friend.account_id
        game_uuid = game.game_uuid
        await save_paipu(account_id, game_uuid, category, type_name)
        url = f"{PP_HOST}{game_uuid}_a{encode_account_id(account_id)}"
        msg = f"{friend.nickname} 结束了在 {place} 的对局\n"

        record = await self.fetch_game_record(game_uuid)
        if record is None:
            if account_id not in self.friends:
                return
            # 中途掉线也会清空 playing：人还离线或已回到这局，就是还没打完
            current = self.friends[account_id]
            if not current.is_online or current.playing.game_uuid == game_uuid:
                return
            await self.send_msg_to_user(str(account_id), f"{msg}获取牌谱失败\n对局牌谱:{url}")
            return

        players = record.head.result.players
        friend_seat, level_id, level_score = 0, 0, 0
        for account in record.head.accounts:
            if account.account_id == account_id:
                level = account.level3 if len(players) == 3 else account.level
                friend_seat, level_id, level_score = account.seat, level.id, level.score
                break
        for rank, player in enumerate(players, 1):
            if player.seat == friend_seat:
                msg += f"排名:{rank} 最终打点:{player.part_point_1} 得点:{player.grading_score}\n"
                if category == 2:
                    level_info = MajsoulLevel(level_id).formatAdjustedScoreWithTag(level_score + player.grading_score)
                    msg += f"当前段位:{level_info}\n"
                break
        msg += f"对局牌谱:{url}"
        await self.send_msg_to_user(str(account_id), msg)

    async def fetch_game_record(self, game_uuid: str) -> liblq.ResGameRecord | None:
        # 刚结束的对局服务端要过几秒才查得到牌谱
        for delay in GAME_RECORD_RETRY_DELAYS:
            await asyncio.sleep(delay)
            resp = await self.rpc_call(
                ".lq.Lobby.fetchGameRecord",
                {"game_uuid": game_uuid, "client_version_string": self.clientVersionString},
            )
            if isinstance(resp, liblq.ResGameRecord) and not resp.error.code:
                return resp
            logger.warning(f"[majs] 获取牌谱 {game_uuid} 失败: {resp}")
        logger.error(f"[majs] 获取牌谱 {game_uuid} 重试 {len(GAME_RECORD_RETRY_DELAYS)} 次仍失败")
        return None

    async def handle_FriendViewChange(self, notify: MajsoulDecodedMessage):
        data = cast(liblq.NotifyFriendViewChange, notify.payload)
        target_user = data.target_id
        changed_base = data.base
        msg = ""
        if target_user in self.friends:
            self.friends[target_user].change_base(changed_base)
        if msg:
            await self.send_msg_to_user(str(target_user), msg)

    async def handle_FriendChange(self, notify: MajsoulDecodedMessage):
        data = cast(liblq.NotifyFriendChange, notify.payload)
        logger.info(f"NotifyFriendChange: {data.__dict__}")
        account_id = data.account_id
        existed = account_id in self.friends
        meta_msg = ""
        if data.type == 1:
            # TODO: The meaning of type 1 is not clear, need to check
            friend = MajsoulFriend(data.friend)
            self.friends = {**self.friends, account_id: friend}
            if existed:
                meta_msg = f"账号已存在好友 {friend.nickname}！"
            else:
                meta_msg = f"账号成功添加好友 {friend.nickname}！"
            if account_id in self.friend_apply_list:
                self.friend_apply_list.remove(account_id)
        elif data.type == 2:
            if existed:
                meta_msg = f"账号成功删除好友 {self.friends[account_id].nickname}！"
                self.friends = {aid: f for aid, f in self.friends.items() if aid != account_id}
        elif existed:
            friend = MajsoulFriend(data.friend)
            self.friends = {**self.friends, account_id: friend}
            meta_msg = f"数据成功更新好友 {friend.nickname}！"
        if meta_msg:
            await self.send_meta(meta_msg)

    async def handle_NewFriendApply(self, notify: MajsoulDecodedMessage):
        data = cast(liblq.NotifyNewFriendApply, notify.payload)
        account_id = data.account_id
        meta_msg = f"收到来自 {account_id} 的好友申请"
        if account_id not in self.friend_apply_list:
            self.friend_apply_list.append(account_id)
        resp = cast(
            liblq.ResMultiAccountBrief,
            await self.rpc_call(
                ".lq.Lobby.fetchMultiAccountBrief",
                {"account_id_list": [account_id]},
            ),
        )
        if resp.error.code:
            meta_msg = f"NotifyNewFriendApply信息序列化错误{account_id}!"
            logger.error(f"{meta_msg}\n{resp.error}")
        else:
            account = resp.players[0]
            meta_msg = f"收到来自 {account.nickname} 的好友申请"
        if meta_msg:
            await self.send_meta(meta_msg)

        if MAJS_CONFIG.get_config("MajsIsAutoApplyFriend").data:
            await self.acceptFriendApply(account_id)

    async def dispatch_msg(self):
        if self._ws is None:
            raise ConnectionError("Connection is broken")

        try:
            while True:
                msg = await self._ws.recv()
                assert isinstance(msg, bytes)
                data = self._codec.decode_message(msg)
                logger.debug(f"[majs] 收到消息, index: {data.req_index}")
                if data.msg_type == self._codec.RESPONSE:
                    idx = data.req_index
                    if idx not in self._req_events:
                        continue
                    self._res[idx] = data
                    self._req_events[idx].set()
                if data.msg_type == self._codec.NOTIFY:
                    try:
                        await self.handle_notify(data)
                    except Exception as e:
                        logger.exception(f"发生错误： {e}")
                    continue
                if data.msg_type == self._codec.REQUEST:
                    logger.info(f"Request: {data}")
                    continue
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[majs] WebSocket连接已断开: {e}")
            self._wake_pending_requests()
        except Exception as e:
            logger.exception(f"[majs] WebSocket消息分发异常: {e}")
            self._wake_pending_requests()

    async def rpc_call(self, method_name: str, payload: dict):
        idx = self._codec.index
        logger.debug(f"[majs] 触发rpc_call, index: {idx}")
        if self.is_closed():
            raise ConnectionError("Connection is broken")

        req = self._codec.encode_request(method_name, payload)

        evt = asyncio.Event()
        self._req_events[idx] = evt
        try:
            await self._ws.send(req)  # type: ignore
            await asyncio.wait_for(evt.wait(), timeout=30)
            if idx not in self._res:
                raise ConnectionError("Connection is broken while waiting rpc response")

            res = self._res[idx]
            return res.payload
        except asyncio.CancelledError:
            raise
        except (websockets.exceptions.ConnectionClosed, asyncio.TimeoutError, ConnectionError) as e:
            logger.warning(f"[majs] rpc_call失败, method={method_name}, index={idx}: {e}")
            raise ConnectionError("Majsoul WebSocket connection lost") from e
        finally:
            self._res.pop(idx, None)
            self._req_events.pop(idx, None)

    async def error_handler(self, error: liblq.Error):
        logger.error(f"[majs] {self.account_id} Connection lost: {error}")
        await manager.restart()

    async def create_heartbeat_task(self) -> None:
        # create a new task to keep the connection alive, 300s heartbeat
        async def heartbeat() -> None:
            while True:
                # random sleep to avoid heartbeat collision
                timeout = random.randint(300, 360)
                await asyncio.sleep(timeout)
                resp = cast(
                    liblq.ResServerTime,
                    await self.rpc_call(".lq.Lobby.fetchServerTime", {}),
                )
                # check if the connection is still alive
                if resp.error.code:
                    await self.error_handler(resp.error)
                resp = cast(
                    liblq.ResCommon,
                    await self.rpc_call(
                        ".lq.Route.heartbeat",
                        {
                            "delay": random.randint(0, 200),
                            "no_operation_counter": 0,
                            "platform": 11,
                            "network_quality": random.randint(0, 100),
                        },
                    ),
                )
                if resp.error.code:
                    await self.error_handler(resp.error)

        task = asyncio.create_task(heartbeat())
        self.bg_tasks.append(task)

    async def process(self):
        while True:
            data = await self.queue.get()
            asyncio.create_task(data)
            self.queue.task_done()

    async def check_connection(self):
        if self._ws is None:
            raise ConnectionError("Connection is broken")
        return True

    def encode_p(self, password: str):
        return hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()

    async def login_beat(self) -> None:
        # 客户端把 loginBeat 失败当成非致命，只重试不登出
        resp = await self.rpc_call(".lq.Lobby.loginBeat", {"contract": LOGIN_BEAT_CONTRACT})
        if not isinstance(resp, liblq.ResCommon) or resp.error.code:
            logger.warning(f"[majs] loginBeat 失败: {resp}")

    async def oauth2_check(self, login_type: int, access_token: str) -> bool:
        for retry in range(2):
            if retry:
                await asyncio.sleep(2)
            resp = await self.rpc_call(
                ".lq.Lobby.oauth2Check",
                {"type": login_type, "access_token": access_token},
            )
            if isinstance(resp, liblq.ResOauth2Check) and resp.has_account:
                return True
            logger.info(f"[majs] oauth2Check 未通过: {resp}")
        return False

    async def oauth2_login(self, login_type: int, access_token: str) -> None:
        resp = await self.rpc_call(
            ".lq.Lobby.oauth2Login",
            {
                "type": login_type,
                "access_token": access_token,
                "reconnect": False,
                **self.login_fields(),
            },
        )
        if not isinstance(resp, liblq.ResLogin) or not resp.account_id:
            raise ValueError(f"Failed to login: {resp}")
        self.account_id = resp.account_id
        self.nick_name = resp.account.nickname

        await self.login_beat()
        logger.info("Connection ready")
        self.access_token = access_token

    async def jp_login(self, uid: str, code: str) -> None:
        resp = await self.rpc_call(
            ".lq.Lobby.oauth2Auth",
            {
                "type": 7,
                "code": code,
                "uid": uid,
                "client_version_string": self.clientVersionString,
            },
        )
        if not isinstance(resp, liblq.ResOauth2Auth) or resp.error.code:
            raise ValueError(f"Failed to oauth2Auth: {resp}")
        access_token = resp.access_token

        if not await self.oauth2_check(7, access_token):
            raise ValueError("Failed to check account")
        await self.oauth2_login(7, access_token)

    async def manual_login(self, username: str, password: str):
        password = self.encode_p(password)
        resp = await self.rpc_call(
            ".lq.Lobby.login",
            {
                "account": username,
                "password": password,
                "reconnect": False,
                "gen_access_token": True,
                **self.login_fields(),
            },
        )
        if not isinstance(resp, liblq.ResLogin) or not resp.account_id:
            raise ValueError(f"Failed to login: {resp}")
        self.account_id = resp.account_id
        self.nick_name = resp.account.nickname
        self.access_token = resp.access_token

        self.manual_login_username = username
        self.manual_login_password = password
        await self.login_beat()
        logger.info("Connection ready")
        return self.account_id, self.access_token

    async def access_token_login(self, access_token: str):
        if not await self.oauth2_check(0, access_token):
            raise ValueError("Failed to check account")
        await self.oauth2_login(0, access_token)

    async def fetchLiveGames(self):
        game_live_list: List[liblq.GameLiveHead] = []
        for gm in GameMode:
            filter_id = f"2{gm.value}"
            games = cast(
                liblq.ResGameLiveList,
                await self.rpc_call(
                    ".lq.Lobby.fetchGameLiveList",
                    {"filter_id": int(filter_id)},
                ),
            )
            game_live_list.extend(games.live_list)
            await asyncio.sleep(0.7)
        return game_live_list

    async def fetchInfo(self):
        resp = cast(
            liblq.ResFetchInfo,
            await self.rpc_call(
                ".lq.Lobby.fetchInfo",
                {},
            ),
        )
        # 服务端给的是全量列表，整体替换；出错时保留旧数据，别清空
        if not resp.error.code and not resp.friend_list.error.code:
            self.friends = {f.base.account_id: MajsoulFriend(f) for f in resp.friend_list.friends}
        if not resp.error.code and not resp.friend_apply_list.error.code:
            self.friend_apply_list = list(dict.fromkeys(a.account_id for a in resp.friend_apply_list.applies))
        return resp

    async def fetchAccountNickname(self, account_id: int) -> str | None:
        resp = await self.rpc_call(
            ".lq.Lobby.fetchMultiAccountBrief",
            {"account_id_list": [account_id]},
        )
        if not isinstance(resp, liblq.ResMultiAccountBrief) or resp.error.code:
            return None
        for player in resp.players:
            if player.account_id == account_id and player.nickname:
                return player.nickname
        return None

    async def acceptFriendApply(self, account_id: int):
        resp = cast(
            liblq.ResCommon,
            await self.rpc_call(
                ".lq.Lobby.handleFriendApply",
                {"method": 1, "target_id": account_id},
            ),
        )
        if account_id in self.friend_apply_list:
            self.friend_apply_list.remove(account_id)
        return resp

    async def fetchLogs(self, game_id: str):
        data = await get_paipu_by_game_id(game_id)
        if data:
            return data

        seps = game_id.split("_")
        log_id = seps[0]

        if len(seps) >= 3 and seps[2] == "2":
            log_id = decode_log_id(log_id)

        target_id = None
        if len(seps) >= 2:
            if seps[1][0] == "a":
                target_id = decode_account_id2(int(seps[1][1:]))
            else:
                target_id = int(seps[1])

        logs = cast(
            liblq.ResGameRecord,
            await self.rpc_call(
                ".lq.Lobby.fetchGameRecord",
                {
                    "game_uuid": log_id,
                    "client_version_string": self.clientVersionString,
                },
            ),
        )
        detail_records = liblq.Wrapper().parse(logs.data)

        payload = liblq.GameDetailRecords().parse(detail_records.data)

        action_list = []
        if payload.version < 210715 and len(payload.records) > 0:
            for value in payload.records:
                raw = liblq.Wrapper().parse(value)
                name = raw.name.split(".")[2]
                msg = getattr(liblq, name)().parse(raw.data)
                item = MjsLogItem(name=name, data=msg)
                action_list.append(item)
        else:
            for action in payload.actions:
                if action.result and len(action.result) > 0:
                    raw = liblq.Wrapper().parse(action.result)
                    name = raw.name.split(".")[2]
                    msg = getattr(liblq, name)().parse(raw.data)
                    item = MjsLogItem(name=name, data=msg)
                    action_list.append(item)

        tenhou_log = MajsoulPaipuParser().handle_game_record(record=MjsLog(logs.head, action_list))

        tenhou_log["head"] = process_dict(logs.head.__dict__)
        tenhou_log["game_id"] = game_id
        tenhou_log["log_id"] = log_id
        tenhou_log["target_id"] = target_id

        logger.info(f"[Majsoul] target_id: {target_id}")
        logger.info(f"[Majsoul] logs.head.accounts: {logs.head.accounts}")

        if target_id is not None:
            for acc in logs.head.accounts:
                if acc.account_id == target_id:
                    tenhou_log["_target_actor"] = acc.seat
                    break

        async with aiofiles.open(
            PAIPU_PATH / f"{game_id} - raw.json",
            "w",
            encoding="utf-8",
        ) as f:
            await f.write(
                json.dumps(
                    dict(tenhou_log),
                    ensure_ascii=False,
                    indent=4,
                )
            )

        return tenhou_log


async def fetch_route(region: MajsoulRegion) -> MajsoulRoute:
    """和 Unity 客户端一样，先向入口网关要路由表，再挑一条路由去连。"""
    params = {
        "platform": DETAIL_PLATFORM,
        "version": CLIENT_VERSION.package_version,
        "lang": region.route_lang,
    }
    headers = {"User-Agent": USER_AGENT, "Referer": region.web_url}
    async with httpx2.AsyncClient(headers=headers, timeout=10) as client:
        for gateway in random.sample(region.gateways, len(region.gateways)):
            try:
                resp = await client.get(f"{gateway}/api/clientgate/routes", params=params)
                resp.raise_for_status()
            except httpx2.HTTPError as e:
                logger.warning(f"[majs] 入口网关 {gateway} 不可用: {e!r}")
                continue
            data = msgspec.convert(resp.json(), MajsoulRoutesResp).data
            if data.maintenance:
                notes = [m.content for m in data.maintenance if m.lang in (region.route_lang, "chs")]
                raise MajsoulMaintenanceError("\n".join(notes or [data.maintenance[0].content]))
            if data.routes:
                return random.choice(data.routes)
            logger.warning(f"[majs] 入口网关 {gateway} 没有返回可用路由")
    raise ConnectionError(f"[majs] {region.tag} 服的入口网关全部不可用")


async def open_connection(region: MajsoulRegion, login_type: int, device_id: str) -> MajsoulConnection:
    route = await fetch_route(region)
    conn = MajsoulConnection(route, region, login_type, MajsoulProtoCodec(RPC_MAP), device_id)
    await conn.connect()
    await conn.request_connection()

    logger.info("Connection established, sending heartbeat")
    _ = await conn.rpc_call(
        ".lq.Route.heartbeat",
        {
            "delay": random.randint(0, 200),
            "no_operation_counter": 0,
            "platform": DETAIL_PLATFORM_ID,
            "network_quality": random.randint(0, 100),
        },
    )
    logger.info(f"Authenticating ({conn.clientVersionString}, liqi {CLIENT_VERSION.liqi_version})")
    return conn


async def createMajsoulConnection(
    username: str = "",
    password: str = "",
    access_token: str = "",
):
    conn = await open_connection(REGION_CN, 0, make_device_id(REGION_CN, username))

    if access_token:
        try:
            await conn.access_token_login(access_token)
        except ValueError as e:
            raise ValueError(f"Access token login failed: {e}")
    else:
        if not username or not password:
            raise ValueError("Username or password is empty")
        try:
            account_id, access_token = await conn.manual_login(username, password)
            await MajsUser.update_data_by_data(
                {"uid": str(account_id)},
                {
                    "cookie": access_token,
                    "username": username,
                    "password": password,
                },
            )
        except ValueError as e:
            raise ValueError(f"Manual login failed: {e}")

    await conn.create_heartbeat_task()

    return conn


async def createYostarMajsoulConnection(uid: str, code: str, lang: str):
    region = yostar_region(lang)
    conn = await open_connection(region, 7, make_device_id(region, uid))
    await conn.jp_login(uid, code)

    await conn.create_heartbeat_task()

    return conn


class MajsoulManager:
    def __init__(self):
        # maybe we need to support multiple connections in the future
        self.conn: list[MajsoulConnection] = []

    async def check_username_password(
        self,
        username: str,
        password: str,
        access_token: str,
        login_type: int,
    ):
        try:
            conn = await createMajsoulConnection(username, password, access_token)
        except MajsoulMaintenanceError as e:
            return f"❌ 登陆失败, 雀魂服务器正在维护中!\ncontext: {e}"
        except ValueError as e:
            logger.error(e)
            return False
        return conn

    async def check_yostar_login(self, uid: str, code: str, lang: str):
        try:
            conn = await createYostarMajsoulConnection(
                uid,
                code,
                lang,
            )
        except MajsoulMaintenanceError as e:
            return f"❌ 登陆失败, 雀魂服务器正在维护中!\ncontext: {e}"
        except ValueError as e:
            logger.error(e)
            return False
        self.conn.append(conn)
        return conn

    async def start(self):
        if len(self.conn) == 0:
            # get all accounts
            users: Sequence[MajsUser] = await MajsUser.get_all_user()
            logger.info(f"[majs] Found {len(users)} accounts to connect")
            logger.debug(f"[majs] Users: {users}")
            for user in users:
                if user.login_type == 0:
                    try:
                        conn = await createMajsoulConnection(username=user.username, access_token=user.cookie)
                    except MajsoulMaintenanceError as e:
                        return f"❌ 登陆失败, 雀魂服务器正在维护中!\ncontext: {e}"
                    except ValueError as e:
                        logger.warning(f"[majs] AccessToken已失效, 使用账密进行刷新！\n{e}")
                        try:
                            conn = await createMajsoulConnection(
                                username=user.username,
                                password=user.password,
                            )
                        except ValueError as e:
                            logger.error(f"[majs] 刷新AccessToken失败, 请重新登录！\n{e}")
                            return "❌ AccessToken已失效, 请重新登录！"

                    self.conn.append(conn)
                    await conn.fetchInfo()
                elif user.login_type == 7:
                    region = yostar_region(user.lang)
                    headers = {
                        "Content-Type": "application/json; charset=utf-8",
                        "User-Agent": USER_AGENT,
                        "Referer": region.web_url,
                        "Origin": region.web_url.rstrip("/"),
                    }
                    # passport uid 存在 account；旧数据只有雀魂 account_id，重新登录一次即可
                    passport_uid = user.account or user.uid
                    if passport_uid is None:
                        logger.error("Yostar UID is None, please check your config")
                        return "❌ Yostar UID is None, 请检查配置"

                    sess = httpx2.AsyncClient(headers=headers, verify=False)
                    url = "https://passport.mahjongsoul.com/user/login"
                    payload = {
                        "uid": passport_uid,
                        "token": user.token,
                        "deviceId": f"web|{passport_uid}",
                    }
                    response = await sess.post(url, json=payload)
                    if response.status_code == 200:
                        res = response.json()
                        if res["result"] == 0:
                            code = res["accessToken"]
                        else:
                            logger.error(res)
                            return "❌ JP Yostar token已失效, 请重新登录！"
                    else:
                        logger.error(response.text)
                        return "❌ JP Yostar token已失效, 请重新登录！"

                    try:
                        conn = await createYostarMajsoulConnection(
                            passport_uid,
                            code,
                            user.lang,
                        )
                    except ValueError as e:
                        logger.warning(f"[majs] Yostar token已失效, 请重新登录！\n{e}")
                        return "❌ Yostar token已失效, 请重新登录！"

                    self.conn.append(conn)
                    await conn.fetchInfo()
        if self.conn and self.conn[0].is_closed():
            logger.warning("[majs] 已有连接已断开，正在自动重启雀魂订阅服务")
            return await self.restart()
        return self.conn[0]

    async def restart(self):
        if self.conn:
            if len(self.conn) >= 1:
                for task in self.conn[0].bg_tasks:
                    task.cancel()  # type: ignore
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
            for conn in self.conn:
                if conn._ws is not None and not conn.is_closed():
                    with contextlib.suppress(Exception):
                        await conn._ws.close()  # type: ignore
        self.conn = []
        return await self.start()

    def get_conn(self):
        conns = self.get_all_conn()
        if conns:
            return conns[0]
        return None
        return self.get_all_conn()[0]

    def get_all_conn(self):
        return self.conn

    async def is_online(self):
        if self.conn == []:
            return False
        return await self.conn[0].check_alive()


manager = MajsoulManager()

if __name__ == "__main__":
    asyncio.run(manager.start())
