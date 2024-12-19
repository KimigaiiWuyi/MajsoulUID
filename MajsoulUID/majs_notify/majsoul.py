import hmac
import json
import uuid
import random
import asyncio
import hashlib
from collections.abc import Iterable
from typing import Dict, Union, cast

import httpx
import aiofiles
import websockets.client
from httpx import AsyncClient
from gsuid_core.gss import gss
from gsuid_core.logger import logger
from msgspec import ValidationError, convert

from .utils import getRes
from ..lib import lq as liblq
from ._level import MajsoulLevel
from .codec import MajsoulProtoCodec
from .majsoul_friend import MajsoulFriend
from .tenhou.parser import MajsoulPaipuParser
from ..majs_config.majs_config import MAJS_CONFIG
from ..utils.resource.RESOURCE_PATH import PAIPU_PATH
from .constants import HEADERS, USER_AGENT, ModeId2Room
from ..utils.database.models import MajsPush, MajsUser, MajsPaipu
from ..utils.api.remote import (
    decode_log_id,
    encode_account_id,
    decode_account_id2,
)
from .model import (
    MjsLog,
    MjsLogItem,
    MajsoulConfig,
    MajsoulResInfo,
    MajsoulUSConfig,
    MajsoulLiqiProto,
    MajsoulServerList,
    MajsoulVersionInfo,
    MajsoulDecodedMessage,
)

PP_HOST = "https://game.maj-soul.com/1/?paipu="


class MajsoulMaintenanceError(Exception):
    pass


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


async def get_paipu_by_game_id(game_id: str) -> Union[Dict, None]:
    path = PAIPU_PATH / f"{game_id} - raw.json"
    if path.exists():
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            data = json.loads(await f.read())
            return data


class MajsoulConnection:
    def __init__(
        self,
        server: str,
        login_type: int,
        codec: MajsoulProtoCodec,
        versionInfo: MajsoulVersionInfo,
    ):
        self._endpoint = server
        self._codec = codec
        self._ws = None
        self._req_events: dict[int, asyncio.Event] = {}
        self._res: dict[int, MajsoulDecodedMessage] = {}
        self.clientVersionString = "web-" + versionInfo.version.replace(
            ".w", ""
        )
        self.no_operation_counter = 0
        self.bg_tasks = []
        self.queue = asyncio.queues.Queue()
        self.account_id = 0
        self.nick_name = ""
        self.friends: list[MajsoulFriend] = []
        self.friend_apply_list: list[int] = []
        self.random_key = str(uuid.uuid4())
        self.login_type = login_type

        self.manual_login_username = ""
        self.manual_login_password = ""
        self.access_token = ""

    async def check_alive(self):
        if self._ws is None:
            return False
        resp = cast(
            liblq.ResCommon,
            await self.rpc_call(
                ".lq.Lobby.heatbeat", {"no_operation_counter": 0}
            ),
        )
        if resp.error.code:
            return False
        return True

    async def start_sv(self):
        await asyncio.gather(self.process(), self.dispatch_msg())

    async def connect(self):
        logger.info(f"Connecting to {self._endpoint}")
        self._ws = await websockets.client.connect(self._endpoint)
        self._msg_dispatcher = asyncio.create_task(self.start_sv())

    async def send_meta(self, meta_msg):
        meta_bot_id = MAJS_CONFIG.get_config("MajsFriendPushBotId").data
        meta_type = MAJS_CONFIG.get_config("MajsFriendPushType").data
        meta_id: str = MAJS_CONFIG.get_config("MajsFriendPushID").data

        if meta_id:
            for BOT_ID in gss.active_bot:
                bot = gss.active_bot[BOT_ID]
                await bot.target_send(
                    meta_msg,
                    meta_type,
                    meta_id,
                    meta_bot_id,
                    "",
                )
        else:
            logger.warning(
                "[majs] 未配置元数据推送对象, 请前往网页控制台配置推送对象!"
            )

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
        def get_playing(
            active_state: liblq.AccountActiveState | liblq.AccountPlayingGame,
        ):
            if isinstance(active_state, liblq.AccountActiveState):
                category = active_state.playing.category
                mode_id = active_state.playing.meta.mode_id
            elif isinstance(active_state, liblq.AccountPlayingGame):
                category = active_state.category
                mode_id = active_state.meta.mode_id
            else:
                err = "only accept AccountActiveState or AccountPlayingGame"
                raise ValueError(err)

            if category == 1:
                type_name = "歹人场"
            elif category == 2:
                type_name = "段位场"
            elif category == 4:
                type_name = "比赛场"
            else:
                type_name = "未知牌谱类型"
            return category, type_name, mode_id

        data = cast(liblq.NotifyFriendStateChange, notify.payload)
        target_user = data.target_id
        active_state = data.active_state
        msg = ""
        for friend in self.friends:
            if friend.account_id == target_user:
                nick_name = friend.nickname
                # find what changed
                if active_state.is_online and not friend.is_online:
                    msg = f"{nick_name} 上线了"
                elif not active_state.is_online and friend.is_online:
                    msg = f"{nick_name} 下线了"

                # if active_state have playing
                active_uuid = active_state.playing.game_uuid
                if active_uuid and not friend.playing.game_uuid:
                    category, type_name, mode_id = get_playing(active_state)

                    room_name = ModeId2Room.get(mode_id, "")
                    if room_name:
                        msg = f"{nick_name} 开始了在 {room_name} 的对局\n"
                    else:
                        msg = f"{nick_name} 开始了在 {type_name} 的对局\n"
                    msg += f"对局id: {active_state.playing.game_uuid}"
                    # save game_uuid
                    if not await MajsPaipu.data_exist(uuid=active_uuid):
                        await MajsPaipu.insert_data(
                            account_id=str(friend.account_id),
                            uuid=active_uuid,
                            paipu_type=category,
                            paipu_type_name=type_name,
                        )

                elif not active_state.playing and friend.playing:
                    category, type_name, mode_id = get_playing(friend.playing)

                    mode_id = friend.playing.meta.mode_id
                    room_name = ModeId2Room.get(mode_id, "")
                    if room_name:
                        msg = f"{nick_name} 结束了在 {room_name} 的对局\n"
                    else:
                        msg = f"{nick_name} 结束了在 {type_name} 的对局\n"
                    uuid = friend.playing.game_uuid
                    encode_aid = encode_account_id(friend.account_id)
                    url = f"{PP_HOST}{uuid}_a{encode_aid}"

                    # check 三麻 or 四麻
                    is_sanma = False
                    if "三" in room_name:
                        is_sanma = True

                    cvs = self.clientVersionString
                    game_record = cast(
                        liblq.ResGameRecord,
                        await self.rpc_call(
                            ".lq.Lobby.fetchGameRecord",
                            {
                                "game_uuid": uuid,
                                "client_version_string": cvs,
                            },
                        ),
                    )

                    # check if game_record is valid
                    if game_record.error.code:
                        # check is_online before send message
                        if not active_state.is_online:
                            friend.change_state(active_state)
                            if not await MajsPaipu.data_exist(
                                uuid=active_uuid
                            ):
                                await MajsPaipu.insert_data(
                                    account_id=str(friend.account_id),
                                    uuid=active_uuid,
                                    paipu_type=category,
                                    paipu_type_name=type_name,
                                )
                            return
                        logger.error(
                            f"获取牌谱失败: {game_record.error}, retrying"
                        )
                        # sleep 1s
                        await asyncio.sleep(1)
                        # retry 1 time
                        cvs = self.clientVersionString
                        game_record = cast(
                            liblq.ResGameRecord,
                            await self.rpc_call(
                                ".lq.Lobby.fetchGameRecord",
                                {
                                    "game_uuid": uuid,
                                    "client_version_string": cvs,
                                },
                            ),
                        )
                        if game_record.error.code:
                            logger.error(f"获取牌谱失败: {game_record.error}")
                            msg += "获取牌谱失败\n"
                            msg += f"对局id: {uuid}"
                            msg += f"对局牌谱:{url}"
                            friend.change_state(active_state)
                            await self.send_msg_to_user(str(target_user), msg)
                            continue

                    accounts = game_record.head.accounts
                    friend_seat = 0
                    friend_level_id = 0
                    friend_score = 0
                    for account in accounts:
                        if account.account_id == friend.account_id:
                            friend_seat = account.seat
                            if is_sanma:
                                friend_level_id = account.level3.id
                                friend_score = account.level3.score
                            else:
                                friend_level_id = account.level.id
                                friend_score = account.level.score
                            break
                    record_result = game_record.head.result.players
                    for i, player in enumerate(record_result):
                        if player.seat == friend_seat:
                            msg += f"排名:{i + 1} "
                            msg += f"最终打点:{player.part_point_1} "
                            msg += f"得点:{player.grading_score}\n"

                            if category == 2:
                                level_info = MajsoulLevel(
                                    friend_level_id
                                ).formatAdjustedScoreWithTag(
                                    friend_score + player.grading_score
                                )
                                msg += f"当前段位:{level_info}\n"
                            break

                    msg += f"对局牌谱:{url}"
                    if not await MajsPaipu.data_exist(uuid=active_uuid):
                        await MajsPaipu.insert_data(
                            account_id=str(friend.account_id),
                            uuid=active_uuid,
                            paipu_type=category,
                            paipu_type_name=type_name,
                        )

                # set friend state
                friend.change_state(active_state)
        if msg:
            await self.send_msg_to_user(str(target_user), msg)

    async def handle_FriendViewChange(self, notify: MajsoulDecodedMessage):
        data = cast(liblq.NotifyFriendViewChange, notify.payload)
        target_user = data.target_id
        changed_base = data.base
        msg = ""
        for friend in self.friends:
            if friend.account_id == target_user:
                # set friend base
                friend.change_base(changed_base)
        if msg:
            await self.send_msg_to_user(str(target_user), msg)

    async def handle_FriendChange(self, notify: MajsoulDecodedMessage):
        data = cast(liblq.NotifyFriendChange, notify.payload)
        logger.info(f"NotifyFriendChange: {data.__dict__}")
        meta_msg = ""
        if data.type == 1:
            # TODO: The meaning of type 1 is not clear, need to check
            for friend in self.friends:
                if friend.account_id == data.account_id:
                    meta_msg = f"好友 {data.account_id} 已在好友列表中！"
                    logger.error(meta_msg)
            else:
                # maybe add friend
                friend = MajsoulFriend(data.friend)
                if friend not in self.friends:
                    self.friends.append(friend)
                    meta_msg = f"账号成功添加好友 {friend.nickname}！"
                    if data.account_id in self.friend_apply_list:
                        self.friend_apply_list.remove(data.account_id)
                else:
                    meta_msg = f"账号已存在好友 {friend.nickname}！"
        elif data.type == 2:
            # 删除好友
            for friend in self.friends:
                if friend.account_id == data.account_id:
                    self.friends.remove(friend)
                    meta_msg = f"账号成功删除好友 {friend.nickname}！"
                    break
        else:
            # check if friend is in self.friends
            for friend in self.friends:
                if friend.account_id == data.account_id:
                    friend = MajsoulFriend(data.friend)
                    meta_msg = f"数据成功更新好友 {friend.nickname}！"
        if meta_msg:
            await self.send_meta(meta_msg)

    async def handle_NewFriendApply(self, notify: MajsoulDecodedMessage):
        data = cast(liblq.NotifyNewFriendApply, notify.payload)
        account_id = data.account_id
        meta_msg = f"收到来自 {account_id} 的好友申请"
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
            if account_id not in self.friend_apply_list:
                self.friend_apply_list.append(account_id)
        if meta_msg:
            await self.send_meta(meta_msg)

        if MAJS_CONFIG.get_config("MajsIsAutoApplyFriend").data:
            await self.acceptFriendApply(account_id)

    async def dispatch_msg(self):
        if self._ws is None:
            raise ConnectionError("Connection is broken")

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

    async def rpc_call(self, method_name: str, payload: dict):
        idx = self._codec.index
        logger.debug(f"[majs] 触发rpc_call, index: {idx}")
        if self._ws is None:
            raise ConnectionError("Connection is broken")

        req = self._codec.encode_request(method_name, payload)

        evt = asyncio.Event()
        self._req_events[idx] = evt
        await self._ws.send(req)
        await evt.wait()

        res = self._res[idx]
        del self._res[idx]
        if idx in self._req_events:
            del self._req_events[idx]

        return res.payload

    async def error_handler(self, error: liblq.Error):
        logger.error(f"[majs] {self.account_id} Connection lost: {error}")
        await manager.restart()

    async def create_heatbeat_task(self):
        # create a new task to keep the connection alive, 300s heartbeat
        async def heartbeat():
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
                        ".lq.Lobby.heatbeat",
                        {"no_operation_counter": 0},
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

    def remove_duplicate_friends(self):
        # friends: List[MajsoulFriend] = []
        unique_friends = {friend.account_id: friend for friend in self.friends}
        self.friends = list(unique_friends.values())
        return self.friends

    def encode_p(self, password: str):
        return hmac.new(
            b"lailai", password.encode(), hashlib.sha256
        ).hexdigest()

    async def jp_login(
        self,
        uid: str,
        code: str,
        version_info: MajsoulVersionInfo,
    ):
        resp = cast(
            liblq.ResOauth2Auth,
            await self.rpc_call(
                ".lq.Lobby.oauth2Auth",
                {
                    "type": 7,
                    "code": code,
                    "uid": uid,
                    "clientVersionString": self.clientVersionString,
                },
            ),
        )
        logger.info(f"OAuth2 Auth: {resp}")
        if resp.error.code:
            raise ValueError(f"Failed to oauth2Auth: {resp}")
        access_token = resp.access_token
        resp = cast(
            liblq.ResOauth2Check,
            await self.rpc_call(
                ".lq.Lobby.oauth2Check",
                {"type": 7, "access_token": access_token},
            ),
        )
        logger.info(f"OAuth2 Check: {resp}")
        if not resp.has_account:
            await asyncio.sleep(2)
            resp = cast(
                liblq.ResOauth2Check,
                await self.rpc_call(
                    ".lq.Lobby.oauth2Check",
                    {"type": 7, "access_token": access_token},
                ),
            )
        if not resp.has_account:
            raise ValueError("Failed to check account")

        resp = cast(
            liblq.ResLogin,
            await self.rpc_call(
                ".lq.Lobby.oauth2Login",
                {
                    "type": 7,
                    "access_token": access_token,
                    "reconnect": False,
                    "device": {
                        "platform": "pc",
                        "hardware": "pc",
                        "os": "windows",
                        "os_version": "win10",
                        "is_browser": True,
                        "software": "Chrome",
                        "sale_platform": "web",
                    },
                    "random_key": self.random_key,
                    "client_version": {"resource": version_info.version},
                    "currency_platforms": [1, 3, 5, 9, 12],
                    "client_version_string": self.clientVersionString,
                    "gen_access_token": False,
                    "tag": "jp",
                },
            ),
        )
        if not resp.account_id:
            raise ValueError("Failed to login")
        self.account_id = resp.account_id
        self.nick_name = resp.account.nickname

        resp = cast(
            liblq.ResCommon,
            await self.rpc_call(
                ".lq.Lobby.loginBeat",
                {"contract": "DF2vkXCnfeXp4WoGSBGNcJBufZiMN3UP"},
            ),
        )
        if resp.error.code:
            raise ValueError(f"Failed to loginBeat: {resp}")
        logger.info("Connection ready")
        self.access_token = access_token

    async def manual_login(
        self,
        username: str,
        password: str,
        version_info: MajsoulVersionInfo,
    ):
        password = self.encode_p(password)
        resp = cast(
            liblq.ResLogin,
            await self.rpc_call(
                ".lq.Lobby.login",
                {
                    "account": username,
                    "password": password,
                    "device": {
                        "platform": "pc",
                        "hardware": "pc",
                        "os": "windows",
                        "os_version": "win10",
                        "is_browser": True,
                        "software": "Chrome",
                        "sale_platform": "web",
                    },
                    "random_key": self.random_key,
                    "client_version": {"resource": version_info.version},
                    "currency_platforms": [2],
                    "client_version_string": self.clientVersionString,
                    "gen_access_token": True,
                },
            ),
        )
        self.account_id = resp.account_id
        self.nick_name = resp.account.nickname
        self.access_token = resp.access_token

        self.manual_login_username = username
        self.manual_login_password = password
        logger.info("Connection ready")
        return self.account_id, self.access_token

    async def access_token_login(
        self,
        version_info: MajsoulVersionInfo,
        access_token: str,
    ):
        resp = cast(
            liblq.ResOauth2Check,
            await self.rpc_call(
                ".lq.Lobby.oauth2Check",
                {"type": 0, "access_token": access_token},
            ),
        )
        logger.info(f"OAuth2 Check: {resp}")
        if not resp.has_account:
            await asyncio.sleep(2)
            resp = cast(
                liblq.ResOauth2Check,
                await self.rpc_call(
                    ".lq.Lobby.oauth2Check",
                    {"type": 0, "access_token": access_token},
                ),
            )
        if not resp.has_account:
            raise ValueError("Failed to check account")

        resp = cast(
            liblq.ResLogin,
            await self.rpc_call(
                ".lq.Lobby.oauth2Login",
                {
                    "type": 0,
                    "access_token": access_token,
                    "reconnect": False,
                    "device": {
                        "platform": "pc",
                        "hardware": "pc",
                        "os": "windows",
                        "os_version": "win10",
                        "is_browser": True,
                        "software": "Chrome",
                        "sale_platform": "web",
                    },
                    "random_key": self.random_key,
                    "client_version": {"resource": version_info.version},
                    "currency_platforms": [],
                    "client_version_string": self.clientVersionString,
                },
            ),
        )
        if not resp.account_id:
            raise ValueError("Failed to login")
        self.account_id = resp.account_id
        self.nick_name = resp.account.nickname

        resp = cast(
            liblq.ResCommon,
            await self.rpc_call(
                ".lq.Lobby.loginBeat",
                {"contract": "DF2vkXCnfeXp4WoGSBGNcJBufZiMN3UP"},
            ),
        )
        if resp.error.code:
            raise ValueError(f"Failed to loginBeat: {resp}")
        logger.info("Connection ready")
        self.access_token = access_token

    async def fetchLiveGames(self):
        game216 = cast(
            liblq.ResGameLiveList,
            await self.rpc_call(
                ".lq.Lobby.fetchGameLiveList",
                {"filter_id": 216},
            ),
        )
        game209 = cast(
            liblq.ResGameLiveList,
            await self.rpc_call(
                ".lq.Lobby.fetchGameLiveList",
                {"filter_id": 209},
            ),
        )
        game212 = cast(
            liblq.ResGameLiveList,
            await self.rpc_call(
                ".lq.Lobby.fetchGameLiveList",
                {"filter_id": 212},
            ),
        )
        return game216.live_list + game209.live_list + game212.live_list

    async def fetchInfo(self):
        resp = cast(
            liblq.ResFetchInfo,
            await self.rpc_call(
                ".lq.Lobby.fetchInfo",
                {},
            ),
        )
        friend_list = resp.friend_list.friends
        if isinstance(friend_list, Iterable):
            for friend in friend_list:
                friend = MajsoulFriend(friend)
                if friend not in self.friends:
                    self.friends.append(friend)
        friend_apply_list = resp.friend_apply_list.applies
        if isinstance(friend_apply_list, Iterable):
            for apply in friend_apply_list:
                self.friend_apply_list.append(apply.account_id)
        return resp

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

        tenhou_log = MajsoulPaipuParser().handle_game_record(
            record=MjsLog(logs.head, action_list)
        )

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


async def fetchMajsoulInfo(URL_BASE: str):
    version_info = convert(
        await getRes(URL_BASE, "version.json", bust_cache=True),
        MajsoulVersionInfo,
    )
    resInfo = convert(
        await getRes(URL_BASE, f"resversion{version_info.version}.json"),
        MajsoulResInfo,
    )
    pbVersion = resInfo.res["res/proto/liqi.json"].prefix
    pbDef = convert(
        await getRes(URL_BASE, f"{pbVersion}/res/proto/liqi.json"),
        MajsoulLiqiProto,
    )
    _path = f'{resInfo.res["config.json"].prefix}/config.json'
    obj = await getRes(URL_BASE, _path)
    try:
        config = convert(
            obj,
            MajsoulUSConfig,
        )
    except ValidationError:
        config = convert(
            obj,
            MajsoulConfig,
        )

    ipDef = next(filter(lambda x: x.name == "player", config.ip))

    serverListUrl = random.choice(ipDef.region_urls).url
    serverListUrl += (
        "?service=ws-gateway&protocol=ws&ssl=true&rv="
        + str(random.random())[2:]
    )

    headers = HEADERS.copy()
    headers["Referer"] = URL_BASE
    resp = await AsyncClient(headers=headers).get(serverListUrl)
    resp.raise_for_status()
    serverList = convert(resp.json(), MajsoulServerList)

    if serverList.maintenance:
        raise MajsoulMaintenanceError(
            serverList.maintenance.message_i18n[0].context
        )
    else:
        assert serverList.servers

        server = random.choice(serverList.servers)
        server += "/gateway"

        return server, pbDef, pbVersion, version_info


async def createMajsoulConnection(
    username: str = "",
    password: str = "",
    access_token: str = "",
):
    URL_BASE = "https://game.maj-soul.com/"

    server, pbDef, pbVersion, version_info = await fetchMajsoulInfo(URL_BASE)

    codec = MajsoulProtoCodec(pbDef, pbVersion)
    conn = MajsoulConnection(f"wss://{server}", 0, codec, version_info)
    await conn.connect()

    logger.info("Connection established, sending heartbeat")
    _ = await conn.rpc_call(".lq.Lobby.heatbeat", {"no_operation_counter": 0})
    logger.info(f"Authenticating ({version_info.version})")

    if access_token:
        try:
            await conn.access_token_login(
                version_info,
                access_token,
            )
        except ValueError as e:
            raise ValueError(f"Access token login failed: {e}")
    else:
        if not username or not password:
            raise ValueError("Username or password is empty")
        try:
            account_id, access_token = await conn.manual_login(
                username,
                password,
                version_info,
            )
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

    await conn.create_heatbeat_task()

    return conn


async def createYostarMajsoulConnection(uid: str, code: str, lang: str):
    URL_BASE = (
        "https://game.mahjongsoul.com/"
        if lang == "jp"
        else "https://mahjongsoul.game.yo-star.com/"
    )

    server, pbDef, pbVersion, version_info = await fetchMajsoulInfo(URL_BASE)

    codec = MajsoulProtoCodec(pbDef, pbVersion)
    conn = MajsoulConnection(f"wss://{server}", 7, codec, version_info)
    await conn.connect()

    logger.info("Connection established, sending heartbeat")
    _ = await conn.rpc_call(".lq.Lobby.heatbeat", {"no_operation_counter": 0})
    logger.info(f"Authenticating ({version_info.version})")

    await conn.jp_login(uid, code, version_info)

    await conn.create_heatbeat_task()

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
            conn = await createMajsoulConnection(
                username, password, access_token
            )
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
            users = await MajsUser.get_all_user()
            for user in users:
                if user.login_type == 0:
                    try:
                        conn = await createMajsoulConnection(
                            access_token=user.cookie
                        )
                    except MajsoulMaintenanceError as e:
                        return (
                            f"❌ 登陆失败, 雀魂服务器正在维护中!\ncontext: {e}"
                        )
                    except ValueError as e:
                        logger.warning(
                            f"[majs] AccessToken已失效, 使用账密进行刷新！\n{e}"
                        )
                        conn = await createMajsoulConnection(
                            username=user.username,
                            password=user.password,
                        )

                    self.conn.append(conn)
                    await conn.fetchInfo()
                elif user.login_type == 7:
                    URL_BASE = (
                        "https://game.mahjongsoul.com/"
                        if user.lang == "jp"
                        else "https://mahjongsoul.game.yo-star.com/"
                    )
                    headers = {
                        "Content-Type": "application/json; charset=utf-8",
                        "User-Agent": USER_AGENT,
                        "Referer": URL_BASE,
                        "Origin": URL_BASE,
                    }
                    sess = httpx.AsyncClient(headers=headers, verify=False)
                    url = "https://passport.mahjongsoul.com/user/login"
                    payload = {
                        "uid": user.uid,
                        "token": user.token,
                        "deviceId": f"web|{user.uid}",
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
                            user.uid,
                            code,
                            user.lang,
                        )
                    except ValueError as e:
                        logger.warning(
                            f"[majs] Yostar token已失效, 请重新登录！\n{e}"
                        )
                        return "❌ Yostar token已失效, 请重新登录！"

                    self.conn.append(conn)
                    await conn.fetchInfo()
        return self.conn[0]

    async def restart(self):
        if self.conn:
            if len(self.conn) >= 1:
                for task in self.conn[0].bg_tasks:
                    task.cancel()  # type: ignore
            for conn in self.conn:
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
