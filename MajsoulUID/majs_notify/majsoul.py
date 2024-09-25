import json
import time
import uuid
import random
import asyncio
from typing import cast
from collections.abc import Iterable

import websockets.client
from msgspec import convert
from httpx import AsyncClient
from gsuid_core.gss import gss
from gsuid_core.logger import logger

from ..lib import lq as liblq
from .codec import MajsoulProtoCodec
from .majsoul_friend import MajsoulFriend
from ..utils.api.remote import PlayerLevel
from .utils import getRes, encodeAccountId
from .constants import HEADERS, ModeId2Room
from ..utils.database.models import MajsPush, MajsUser
from .model import (
    MajsoulConfig,
    MajsoulResInfo,
    MajsoulLiqiProto,
    MajsoulServerList,
    MajsoulVersionInfo,
    MajsoulDecodedMessage,
)

PP_HOST = "https://game.maj-soul.com/1/?paipu="


class MajsoulConnection:
    def __init__(
        self,
        server: str,
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
        self.account_id = 0
        self.nick_name = ""
        self.friends: list[MajsoulFriend] = []
        self.friend_apply_list: list[int] = []
        self.last_heartbeat_time = 0

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

    async def connect(self):
        logger.info(f"Connecting to {self._endpoint}")
        self._ws = await websockets.client.connect(self._endpoint)
        self._msg_dispatcher = asyncio.create_task(self.dispatch_msg())

    async def handle_notify(self, notify: MajsoulDecodedMessage):
        logger.info(f"Notify: {notify}")
        msg = ""
        if notify.method_name == ".lq.NotifyFriendStateChange":
            data = cast(liblq.NotifyFriendStateChange, notify.payload)
            target_user = data.target_id
            active_state = data.active_state
            for friend in self.friends:
                if friend.account_id == target_user:
                    nick_name = friend.nickname
                    # find what changed
                    if active_state.is_online and not friend.is_online:
                        msg = f"{nick_name} 上线了"
                    elif not active_state.is_online and friend.is_online:
                        msg = f"{nick_name} 下线了"

                    try:
                        with open("game_record.json", encoding="utf8") as f:
                            game_record = json.load(f)
                    except FileNotFoundError:
                        game_record = {}
                    except json.JSONDecodeError:
                        game_record = {}

                    # if active_state have playing
                    active_uuid = active_state.playing.game_uuid
                    if active_uuid and not friend.playing.game_uuid:
                        category = active_state.playing.category
                        mode_id = active_state.playing.meta.mode_id
                        if category == 1:
                            msg = f"{nick_name} 开始了歹人场"
                        elif category == 2:
                            msg = f"{nick_name} 开始了段位场"
                        elif category == 4:
                            msg = f"{nick_name} 开始了比赛场"
                        else:
                            msg = f"{nick_name} 未知牌谱类别 {category}"
                        rome_name = ModeId2Room.get(mode_id, "")
                        msg += f" {rome_name} mod_id: {mode_id}\n"
                        msg += f"对局id: {active_state.playing.game_uuid}"
                        # save game_uuid
                        game_record[active_state.playing.game_uuid] = (
                            friend.account_id
                        )
                    elif not active_state.playing and friend.playing:
                        with open("game_record.json", encoding="utf8") as f:
                            game_record = json.load(f)
                        mode_id = friend.playing.meta.mode_id
                        room_name = ModeId2Room.get(mode_id, "")
                        msg = f"{nick_name} 结束了在 {room_name} 的对局\n"
                        uuid = friend.playing.game_uuid
                        encode_aid = encodeAccountId(friend.account_id)
                        url = f"{PP_HOST}{uuid}_a{encode_aid}"
                        msg += f"牌谱为 {url}\n"

                        # also save game_uuid
                        game_record[friend.playing.game_uuid] = (
                            friend.account_id
                        )
                    with open("game_record.json", "w", encoding="utf8") as f:
                        json.dump(game_record, f)
                    # set friend state
                    friend.change_state(active_state)
        elif notify.method_name == ".lq.NotifyFriendViewChange":
            data = cast(liblq.NotifyFriendViewChange, notify.payload)
            target_user = data.target_id
            changed_base = data.base
            for friend in self.friends:
                if friend.account_id == target_user:
                    nick_name = friend.nickname
                    need_send = False
                    msg = ""
                    # check level change
                    changed_level = changed_base.level.id
                    changed_level3 = changed_base.level3.id
                    if changed_level != friend.level.id:
                        level_info = PlayerLevel(
                            changed_level, changed_base.level.score
                        ).getTag()
                        msg = f"{nick_name} 的四麻段位更新为 {level_info}\n"
                        need_send = True
                    if changed_level3 != friend.level3.id:
                        level_info = PlayerLevel(
                            changed_level3, changed_base.level3.score
                        ).getTag()
                        msg = f"{nick_name} 的三麻段位更新为 {level_info}\n"
                        need_send = True

                    changed_score = changed_base.level.score
                    changed_score3 = changed_base.level3.score
                    if changed_score != friend.level.score:
                        need_send = True
                        # 四麻
                        level_info = PlayerLevel(
                            changed_level, changed_score
                        ).formatAdjustedScoreWithTag(changed_score)
                        score_change = changed_score - friend.level.score

                        msg += f"四麻段位信息: {level_info}\n"
                        if score_change >= 0:
                            msg += f"增加了 {score_change}"
                        else:
                            msg += f"减少了 {-score_change}"
                    elif changed_score3 != friend.level3.score:
                        need_send = True
                        # 三麻
                        level_info = PlayerLevel(
                            changed_level3, changed_score3
                        ).formatAdjustedScoreWithTag(changed_score3)
                        score_change = changed_score3 - friend.level3.score
                        msg += f"三麻段位信息: {level_info}\n"
                        if score_change >= 0:
                            msg += f"增加了 {score_change}"
                        else:
                            msg += f"减少了 {-score_change}"

                    # set friend base
                    friend.change_base(changed_base)
                    if not need_send:
                        return
        elif notify.method_name == ".lq.NotifyNewFriendApply":
            data = cast(liblq.NotifyNewFriendApply, notify.payload)
            account_id = data.account_id
            # use rpc call to get info
            resp = cast(
                liblq.ResMultiAccountBrief,
                await self.rpc_call(
                    ".lq.Lobby.fetchMultiAccountBrief",
                    {"account_id_list": [account_id]},
                ),
            )
            if resp.error.code:
                msg = f"Error fetchMultiAccountBrief when NotifyNewFriendApply for {account_id}, {resp.error}"
                logger.error(msg)
            else:
                account = resp.players[0]
                msg = f"收到来自 {account.nickname} 的好友申请"
                self.friend_apply_list.append(account_id)
        elif notify.method_name == ".lq.NotifyFriendChange":
            data = cast(liblq.NotifyFriendChange, notify.payload)
            if data.type == 1:
                # TODO: The meaning of type 1 is not clear, need to check
                # add a check if friend is in self.friends
                for friend in self.friends:
                    if friend.account_id == data.account_id:
                        logger.error(
                            f"Error: friend {data.account_id} already in self.friends"
                        )
                        msg = "Error: friend already in self.friends, please check .lq.NotifyFriendChange"
                # maybe add friend
                friend = MajsoulFriend(data.friend)
                self.friends.append(friend)
                msg = f"Successfully add friend {friend.nickname}"
            else:
                # check if friend is in self.friends
                for friend in self.friends:
                    if friend.account_id == data.account_id:
                        friend = MajsoulFriend(data.friend)
                        msg = f"Successfully update friend {friend.nickname}"
                        # Maybe more msg info about friend update?
        elif notify.method_name == ".lq.NotifyAnotherLogin":
            data = cast(liblq.NotifyAnotherLogin, notify.payload)
            msg = f"Current account {self.nick_name}({self.account_id}) has been logged in elsewhere\n"
            msg += "Access Token maybe expired, please check"
        else:
            msg = notify.payload.to_json()
            if msg == "{}":
                return

        if not msg:
            return

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

    async def dispatch_msg(self):
        if self._ws is None:
            raise ConnectionError("Connection is broken")

        while True:
            msg = await self._ws.recv()
            assert isinstance(msg, bytes)
            data = self._codec.decode_message(msg)
            if data.msg_type == self._codec.RESPONSE:
                idx = data.req_index
                if idx not in self._req_events:
                    continue
                self._res[idx] = data
                self._req_events[idx].set()
            if data.msg_type == self._codec.NOTIFY:
                await self.handle_notify(data)
                continue
            if data.msg_type == self._codec.REQUEST:
                logger.info(f"Request: {data}")
                continue

    async def rpc_call(self, method_name: str, payload: dict):
        idx = self._codec.index

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

    def get_no_operation_counter(self):
        current_time = time.time()
        no_operation_counter = current_time - self.last_heartbeat_time
        self.last_heartbeat_time = current_time
        return no_operation_counter

    async def check_connection(self):
        if self._ws is None:
            raise ConnectionError("Connection is broken")
        return True

    async def accessTokenLogin(
        self,
        versionInfo: MajsoulVersionInfo,
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
                    "random_key": str(uuid.uuid4()),
                    "client_version": {"resource": versionInfo.version},
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
                self.friends.append(MajsoulFriend(friend))
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
        return resp


async def createMajsoulConnection(access_token: str):
    versionInfo = convert(
        await getRes("version.json", bust_cache=True),
        MajsoulVersionInfo,
    )
    resInfo = convert(
        await getRes(f"resversion{versionInfo.version}.json"),
        MajsoulResInfo,
    )
    pbVersion = resInfo.res["res/proto/liqi.json"].prefix
    pbDef = convert(
        await getRes(f"{pbVersion}/res/proto/liqi.json"),
        MajsoulLiqiProto,
    )
    config = convert(
        await getRes(f'{resInfo.res["config.json"].prefix}/config.json'),
        MajsoulConfig,
    )
    ipDef = next(filter(lambda x: x.name == "player", config.ip))

    serverListUrl = random.choice(ipDef.region_urls).url
    serverListUrl += (
        "?service=ws-gateway&protocol=ws&ssl=true&rv="
        + str(random.random())[2:]
    )

    resp = await AsyncClient(headers=HEADERS).get(serverListUrl)
    resp.raise_for_status()
    serverList = convert(resp.json(), MajsoulServerList)

    server = random.choice(serverList.servers)
    if server.find("maj-soul") > -1:
        server += "/gateway"

    codec = MajsoulProtoCodec(pbDef, pbVersion)
    conn = MajsoulConnection(f"wss://{server}", codec, versionInfo)
    await conn.connect()

    logger.info("Connection established, sending heartbeat")
    _ = await conn.rpc_call(".lq.Lobby.heatbeat", {"no_operation_counter": 0})
    logger.info(f"Authenticating ({versionInfo.version})")
    await conn.accessTokenLogin(versionInfo, access_token)

    # create a new task to keep the connection alive, 300s heartbeat
    async def heartbeat():
        while True:
            # random sleep to avoid heartbeat collision
            await asyncio.sleep(360)
            resp = await conn.rpc_call(".lq.Lobby.fetchServerTime", {})
            logger.info(resp)
            resp = await conn.rpc_call(
                ".lq.Lobby.heatbeat",
                {"no_operation_counter": 0},
            )
            logger.info(resp)

    task = asyncio.create_task(heartbeat())
    conn.bg_tasks.append(task)

    return conn


class MajsoulManager:
    def __init__(self):
        # maybe we need to support multiple connections in the future
        self.conn: list[MajsoulConnection] = []

    async def check_access_token(self, access_token: str):
        try:
            conn = await createMajsoulConnection(access_token)
        except ValueError as e:
            logger.error(e)

            return False
        return conn.account_id

    async def start(self):
        if len(self.conn) == 0:
            cookies = await MajsUser.get_all_cookie()
            if cookies:
                for access_token in cookies:
                    conn = await createMajsoulConnection(access_token)
                    self.conn.append(conn)
                    await conn.fetchInfo()
            else:
                return (
                    "❌错误: 未找到有效的ACCESS_TOKEN！请先进行[雀魂添加账号]"
                )
        return self.conn[0]

    async def restart(self):
        for task in self.conn[0].bg_tasks:
            task.cancel()
        for conn in self.conn:
            await conn._ws.close()  # type: ignore
        self.conn = []
        return await self.start()

    def get_conn(self):
        return self.conn[0]

    async def is_online(self):
        if self.conn is []:
            return False
        return await self.conn[0].check_alive()


manager = MajsoulManager()

if __name__ == "__main__":
    asyncio.run(manager.start())
