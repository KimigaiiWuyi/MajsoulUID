import hmac
import json
import time
import uuid
import random
import asyncio
import hashlib
from enum import Enum
from typing import List, Callable
from dataclasses import dataclass

import httpx2
import msgspec
import aiofiles
import betterproto

from gsuid_core.gss import gss
from gsuid_core.logger import logger
from gsuid_core.subscribe import gs_subscribe

from ..lib import lq as liblq
from .codec import CLIENT_VERSION, message_type
from .model import (
    MjsLog,
    MjsLogItem,
    YostarPassportResp,
    MajsoulDecodedMessage,
)
from .route import RespT, RouteSocket, spawn
from ._level import MajsoulLevel
from .constants import (
    REGION_CN,
    USER_AGENT,
    SERVER_NAME,
    SOCIO_TYPE_CN,
    ERR_PROHIBITED,
    IGNORED_NOTIFY,
    CLIENT_PLATFORM,
    ERR_LOGIN_QUEUE,
    LOGIN_TYPE_PHONE,
    RECORD_ERROR_TEXT,
    SOCIO_TYPE_YOSTAR,
    LOGIN_TYPE_ACCOUNT,
    LOBBY_PING_INTERVAL,
    LOGIN_BEAT_ATTEMPTS,
    LOGIN_BEAT_CONTRACT,
    SERVER_TIME_INTERVAL,
    HANGUP_LOGOUT_SECONDS,
    ERR_RECORD_FETCH_LIMIT,
    LOGIN_BEAT_RETRY_DELAY,
    LOGIN_QUEUE_MAX_ERRORS,
    LOGIN_QUEUE_POLL_INTERVAL,
    ModeId2Room,
    MajsoulRegion,
    yostar_region,
)
from .paipu_cache import PAIPU_CACHE_VERSION, get_paipu_by_game_id
from .route_group import SOCKET_ERRORS, RouteGroup, GatewayFetcher
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
PASSPORT_LOGIN_URL = "https://passport.mahjongsoul.com/user/login"
TASK_NAME_MAJSOUL_NOTIFY = "订阅雀魂消息推送"
# 第一次立刻查，之后逐次拉长间隔，总共等一分多钟
GAME_RECORD_RETRY_DELAYS = (0, 3, 5, 10, 20, 30)
# 牌谱接口有拉取限额（540），和牌谱屋爬虫一样两次之间至少隔 1 秒
RECORD_FETCH_INTERVAL = 1.0
# 同一局的结果留这么久，几个好友同局时只查一次
GAME_HEAD_CACHE_SECONDS = 600
# 连路由、发请求、问网关时可能遇到的网络错误
NETWORK_ERRORS = (*SOCKET_ERRORS, httpx2.HTTPError)


class MajsoulLoginError(Exception):
    """登录失败，文案直接给用户看。"""


class MajsoulMaintenanceError(MajsoulLoginError):
    pass


class MajsoulBannedError(MajsoulLoginError):
    pass


class ConnState(Enum):
    OFFLINE = "未登录"
    ONLINE = "在线"
    RECONNECTING = "重连中"
    FAILED = "已断开"
    KICKED = "被顶号"


@dataclass
class MajsoulCredential:
    login_type: int
    username: str = ""
    # 原文密码：官方客户端也这样存（UI_Entrance.SetPrefs('password', ...)），发请求时才算 HMAC
    password: str = ""
    access_token: str = ""
    passport_uid: str = ""
    passport_token: str = ""
    lang: str = "zh"

    @property
    def region(self) -> MajsoulRegion:
        return yostar_region(self.lang) if self.login_type == SOCIO_TYPE_YOSTAR else REGION_CN

    @property
    def identity(self) -> str:
        return self.passport_uid if self.login_type == SOCIO_TYPE_YOSTAR else self.username

    def same_account(self, other: "MajsoulCredential") -> bool:
        return bool(self.identity) and (self.login_type, self.identity) == (other.login_type, other.identity)


class IdleSimulator:
    """模拟 GameMgr 心跳里的挂机秒数（Time.time - last_moveXY_time）。

    真人隔一阵就会动鼠标；挂机满 50 分钟官方客户端会自己弹登出，所以永远报不到 50 分钟。
    """

    # 插件自己定的：两次挂机心跳之间动过鼠标的概率
    MOVE_CHANCE = 0.6

    def __init__(self) -> None:
        self._last_move = time.monotonic()

    def sample(self, now: float) -> int:
        window_start = max(self._last_move, now - LOBBY_PING_INTERVAL)
        must_move = now - self._last_move + LOBBY_PING_INTERVAL >= HANGUP_LOGOUT_SECONDS
        if must_move or random.random() < self.MOVE_CHANCE:
            self._last_move = random.uniform(window_start, now)
        return int(now - self._last_move)


def make_device_id(region: MajsoulRegion, identity: str) -> str:
    # 真实客户端的 device_id 存在本地、换次登录也不变，这里按账号固定一个
    if not identity:
        return str(uuid.uuid4())
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"majsoul:{region.tag}:{identity}"))


def encode_password(password: str) -> str:
    # LoginMgr：LuaTools.HmacSHA256(password, "lailai")
    return hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()


def passport_headers(region: MajsoulRegion) -> dict[str, str]:
    return {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": USER_AGENT,
        "Referer": region.web_url,
        "Origin": region.origin,
    }


async def yostar_passport_post(region: MajsoulRegion, url: str, payload: dict[str, str]) -> YostarPassportResp:
    async with httpx2.AsyncClient(headers=passport_headers(region), timeout=30) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code != 200:
        raise MajsoulLoginError(f"Yostar passport 返回 HTTP {resp.status_code}")
    try:
        return msgspec.json.decode(resp.content, type=YostarPassportResp)
    except msgspec.DecodeError as e:
        raise MajsoulLoginError(f"Yostar passport 返回内容无法解析: {resp.text[:200]}") from e


async def yostar_passport_login(region: MajsoulRegion, uid: str, token: str) -> str:
    """用 passport 的 uid + token 换一次性 code，给 oauth2Auth 用。"""
    data = await yostar_passport_post(
        region, PASSPORT_LOGIN_URL, {"uid": uid, "token": token, "deviceId": f"web|{uid}"}
    )
    if data.result != 0 or not data.accessToken:
        raise MajsoulLoginError(f"Yostar token 已失效（result={data.result}），请重新[雀魂登录日服]")
    return data.accessToken


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


def record_error_text(code: int) -> str:
    return RECORD_ERROR_TEXT[code] if code in RECORD_ERROR_TEXT else f"获取牌谱失败（错误码 {code}）"


async def save_paipu(account_id: int, game_uuid: str, category: int, type_name: str) -> None:
    if not await MajsPaipu.data_exist(uuid=game_uuid):
        await MajsPaipu.insert_data(
            account_id=str(account_id),
            uuid=game_uuid,
            paipu_type=category,
            paipu_type_name=type_name,
        )


async def wait_event(event: asyncio.Event, timeout: float) -> bool:
    waiter = asyncio.create_task(event.wait())
    try:
        await asyncio.wait({waiter}, timeout=timeout)
    finally:
        # 超时或外面被取消时都要收掉，不然 event.wait() 一直挂着
        waiter.cancel()
    return event.is_set()


class MajsoulConnection:
    """账号池里的一个小号（LobbyNetMgr + LoginMgr 那一层）。

    线路由 RouteGroup 管，断线重连、切备用线路都不动这里的好友和推送状态。
    """

    def __init__(self, credential: MajsoulCredential, device_id: str):
        self.credential = credential
        self._region = credential.region
        self.random_key = device_id
        self.clientVersionString = f"{CLIENT_PLATFORM}_2022-{CLIENT_VERSION.resource_version}"
        self.state = ConnState.OFFLINE
        self.last_error = ""
        self.account_id = 0
        self.nick_name = ""
        self.access_token = credential.access_token
        self._gateway = GatewayFetcher(self._region)
        self._group: RouteGroup | None = None
        # 上次用的主线路（客户端存在 ROUTE_KEY 里），重新登录时优先用
        self._preferred_route = ""
        self._kick_relogin_used = False
        self._idle = IdleSimulator()
        self._loops: list[asyncio.Task[None]] = []
        self._notify_tasks: set[asyncio.Task[None]] = set()
        self._side_tasks: set[asyncio.Task[None]] = set()
        self._kicked_socket: RouteSocket | None = None
        self._queue_finished = asyncio.Event()
        self._login_lock = asyncio.Lock()
        # 增删一律换新 dict：好友状态推送会在 await 中途遍历它
        self.friends: dict[int, MajsoulFriend] = {}
        self.friend_apply_list: list[int] = []
        # 好友 id → (等待中的上下线推送, 开始抖动前的在线状态)
        self._online_pending: dict[int, tuple[asyncio.Task[None], bool]] = {}
        self._record_lock = asyncio.Lock()
        self._last_record_fetch = 0.0
        self._game_heads: dict[str, asyncio.Task[liblq.RecordGame | None]] = {}

    @property
    def label(self) -> str:
        if self.account_id:
            return f"{self.nick_name}({self.account_id})"
        return self.credential.identity or "未知账号"

    def status_line(self) -> str:
        server = SERVER_NAME[self.credential.lang] if self.credential.lang in SERVER_NAME else self.credential.lang
        icon = {ConnState.ONLINE: "✅", ConnState.RECONNECTING: "🔄"}
        line = f"{icon[self.state] if self.state in icon else '❌'} {server} {self.label}：{self.state.value}"
        if self.state in (ConnState.FAILED, ConnState.KICKED) and self.last_error:
            line += f"（{self.last_error}）"
        return line

    def is_closed(self) -> bool:
        return self.state is not ConnState.ONLINE

    def reset_kick(self) -> None:
        self._kick_relogin_used = False

    @property
    def main_socket(self) -> RouteSocket | None:
        return self._group.main if self._group is not None else None

    async def rpc_call(self, method_name: str, req: betterproto.Message, resp_type: type[RespT]) -> RespT:
        if self._group is None:
            raise ConnectionError("[majs] 还没有连上雀魂")
        return await self._group.request(method_name, req, resp_type)

    # ---------- 登录（UI_Entrance / LoginMgr） ----------

    def _device(self) -> liblq.ClientDeviceInfo:
        return liblq.ClientDeviceInfo(
            platform="pc",
            hardware="unknown",
            os="windows",
            os_version="Windows 10 64bit",
            is_browser=True,
            sale_platform="web",
            user_agent=USER_AGENT,
            screen_width=1920,
            screen_height=1080,
            screen_type=1,
            device_id=self.random_key,
        )

    def _client_version(self) -> liblq.ClientVersionInfo:
        return liblq.ClientVersionInfo(
            resource=CLIENT_VERSION.resource_version,
            package=CLIENT_VERSION.package_version,
        )

    def _oauth2_login_req(self, token: str, reconnect: bool) -> liblq.ReqOauth2Login:
        return liblq.ReqOauth2Login(
            type=self.credential.login_type,
            access_token=token,
            reconnect=reconnect,
            device=self._device(),
            random_key=self.random_key,
            client_version=self._client_version(),
            currency_platforms=list(self._region.currency_platforms),
            client_version_string=self.clientVersionString,
            tag=self._region.tag,
        )

    def _password_login_req(self) -> liblq.ReqLogin:
        username = self.credential.username
        return liblq.ReqLogin(
            account=username,
            password=encode_password(self.credential.password),
            reconnect=False,
            device=self._device(),
            random_key=self.random_key,
            client_version=self._client_version(),
            gen_access_token=True,
            currency_platforms=list(self._region.currency_platforms),
            type=LOGIN_TYPE_PHONE if username.isdigit() else LOGIN_TYPE_ACCOUNT,
            client_version_string=self.clientVersionString,
            tag=self._region.tag,
        )

    async def login(self) -> None:
        """全新登录：问网关要路由、连上，再按账号类型登录。失败抛 MajsoulLoginError。"""
        # 登录中途被顶号时，被顶号的处理也会来登录，两次登录要排队，不然会关掉对方的连接
        async with self._login_lock:
            await self._login()

    async def _login(self) -> None:
        await self._shutdown()
        self.state = ConnState.OFFLINE
        try:
            group = RouteGroup(self._region, self._gateway, self)
            self._group = group
            await self._open_entrance(group)
            resp = await self._fresh_login()
            await self._apply_login(resp)
            # LoginMgr.OnLoginSuccess 末尾发 loginBeat；UI_Entrance:OnLoginSuccess 有昵称才报 loginSuccess
            await self.login_beat()
            if self.nick_name:
                await self._send_login_success()
            await self.fetchInfo()
        except MajsoulLoginError as e:
            await self._fail(str(e))
            raise
        except NETWORK_ERRORS as e:
            await self._fail(f"网络错误：{e!r}")
            raise MajsoulLoginError(f"网络错误：{e!r}") from e
        except Exception as e:
            # 不是登录失败也不是网络问题（多半是 bug）：先关连接、记下状态，再原样抛出去
            await self._fail(f"登录时出错：{e!r}")
            raise
        self._idle = IdleSimulator()
        self.state = ConnState.ONLINE
        self._start_loops()
        # LobbyNetMgr.InitOnLoginSuccess：登录成功后线路组才开始管重连和备用线路
        group.start()
        logger.info(f"[majs] {self.label} 登录成功")

    async def _open_entrance(self, group: RouteGroup) -> None:
        # 和打开游戏一样先拉一遍线路表；维护中不连（NetRouteGroup_Entrance）
        if not await self._gateway.fetch():
            raise ConnectionError(f"[majs] {self._region.tag} 服的入口网关全部不可用")
        if self._gateway.maintenance:
            raise MajsoulMaintenanceError(f"雀魂服务器正在维护：{self._gateway.maintenance}")
        main = await group.open_entrance(self._preferred_route)
        self._preferred_route = main.route.id

    async def _fresh_login(self) -> liblq.ResLogin:
        cred = self.credential
        if cred.login_type == SOCIO_TYPE_YOSTAR:
            code = await yostar_passport_login(self._region, cred.passport_uid, cred.passport_token)
            auth = await self.rpc_call(
                ".lq.Lobby.oauth2Auth",
                liblq.ReqOauth2Auth(
                    type=SOCIO_TYPE_YOSTAR,
                    code=code,
                    uid=cred.passport_uid,
                    client_version_string=self.clientVersionString,
                ),
                liblq.ResOauth2Auth,
            )
            if auth.error.code or not auth.access_token:
                raise MajsoulLoginError(f"Yostar 授权失败（错误码 {auth.error.code}）")
            resp = await self._token_login(auth.access_token)
            if resp is None:
                raise MajsoulLoginError("Yostar 账号校验没通过，请重新[雀魂登录日服]")
            return resp
        if self.access_token:
            resp = await self._token_login(self.access_token)
            if resp is not None:
                return resp
            logger.warning(f"[majs] {cred.username} 的 AccessToken 已失效，改用账密登录")
        if not cred.password:
            raise MajsoulLoginError("AccessToken 已失效，请重新[雀魂登陆国服]")
        # 插件自己的逻辑：官方 token 失效会回登录页让人点登录，这里直接用存下的账密
        resp = await self._send_login(".lq.Lobby.login", self._password_login_req())
        if resp.error.code or not resp.account_id:
            raise MajsoulLoginError(f"账密登录失败（错误码 {resp.error.code}）")
        return resp

    async def _token_login(self, token: str) -> liblq.ResLogin | None:
        # UI_Entrance:CheckToken：oauth2Check 只发一次，出错或没有账号就回登录页
        check = await self.rpc_call(
            ".lq.Lobby.oauth2Check",
            liblq.ReqOauth2Check(type=self.credential.login_type, access_token=token),
            liblq.ResOauth2Check,
        )
        if check.error.code or not check.has_account:
            logger.info(f"[majs] oauth2Check 未通过: {check}")
            return None
        resp = await self._send_login(".lq.Lobby.oauth2Login", self._oauth2_login_req(token, reconnect=False))
        if resp.error.code or not resp.account_id:
            logger.info(f"[majs] oauth2Login 失败: {resp.error}")
            return None
        self.access_token = token
        return resp

    async def _send_login(self, method_name: str, req: liblq.ReqLogin | liblq.ReqOauth2Login) -> liblq.ResLogin:
        """503 是封禁；156 要先排队，排到了再用同一个请求登录（UI_Entrance / UI_LoginQueue）。"""
        while True:
            resp = await self.rpc_call(method_name, req, liblq.ResLogin)
            if resp.error.code == ERR_PROHIBITED:
                raise MajsoulBannedError(f"账号被封禁：{resp.error}")
            if resp.error.code != ERR_LOGIN_QUEUE:
                return resp
            logger.info(f"[majs] {self.label} 登录需要排队")
            await self._wait_login_queue()

    async def _wait_login_queue(self) -> None:
        # UI_LoginQueue：先拉一次排队信息，之后每 10 秒刷新，等 NotifyLoginQueueFinished
        # 官方回调里错误码写成了 m.erro，实际只有请求本身失败才算出错，这里照做
        self._queue_finished.clear()
        await self.rpc_call(".lq.Lobby.fetchQueueInfo", liblq.ReqCommon(), liblq.ResFetchQueueInfo)
        errors = 0
        while not await wait_event(self._queue_finished, LOGIN_QUEUE_POLL_INTERVAL):
            try:
                info = await self.rpc_call(".lq.Lobby.fetchQueueInfo", liblq.ReqCommon(), liblq.ResFetchQueueInfo)
            except NETWORK_ERRORS as e:
                errors += 1
                if errors > LOGIN_QUEUE_MAX_ERRORS:
                    raise MajsoulLoginError("登录排队时服务器连接异常，请稍后重试") from e
                continue
            errors = 0
            logger.info(f"[majs] {self.label} 排队中：第 {info.rank} 位，约 {info.remain} 秒")

    async def _apply_login(self, resp: liblq.ResLogin) -> None:
        self.account_id = resp.account_id
        self.nick_name = resp.account.nickname
        # LoginMgr.OnLoginSuccess：回包带了新 access_token 就换掉存下的
        if resp.access_token and resp.access_token != self.access_token:
            self.access_token = resp.access_token
            # 写库放后台：写失败只记日志，不能卡住登录、重连的状态
            task = spawn(self._save_access_token(), f"majs-save-token-{self.account_id}")
            self._side_tasks.add(task)
            task.add_done_callback(self._side_tasks.discard)

    async def _save_access_token(self) -> None:
        await MajsUser.update_data_by_data({"uid": str(self.account_id)}, {"cookie": self.access_token})

    async def login_beat(self) -> None:
        # LoginMgr._SendLoginBeat：失败隔 1 秒重发，最多发 2 次，都失败也不登出
        for attempt in range(LOGIN_BEAT_ATTEMPTS):
            if attempt:
                await asyncio.sleep(LOGIN_BEAT_RETRY_DELAY)
            try:
                resp = await self.rpc_call(
                    ".lq.Lobby.loginBeat", liblq.ReqLoginBeat(contract=LOGIN_BEAT_CONTRACT), liblq.ResCommon
                )
            except TimeoutError:
                continue
            if not resp.error.code:
                return
            logger.warning(f"[majs] loginBeat 失败: {resp.error}")

    async def _send_login_success(self) -> None:
        resp = await self.rpc_call(".lq.Lobby.loginSuccess", liblq.ReqCommon(), liblq.ResCommon)
        if resp.error.code:
            logger.warning(f"[majs] loginSuccess 失败: {resp.error}")

    # ---------- 心跳、重连与线路事件（GameMgr / LobbyNetMgr） ----------

    def _start_loops(self) -> None:
        self._stop_loops()
        self._loops = [
            spawn(self._lobby_ping_loop(), f"majs-lobby-ping-{self.account_id}"),
            spawn(self._server_time_loop(), f"majs-server-time-{self.account_id}"),
        ]

    def _stop_loops(self) -> None:
        for task in self._loops:
            task.cancel()
        self._loops = []

    async def _lobby_ping_loop(self) -> None:
        while True:
            await asyncio.sleep(LOBBY_PING_INTERVAL)
            socket = self.main_socket
            if socket is None or not socket.is_open() or self.state is not ConnState.ONLINE:
                continue
            try:
                await socket.send_heartbeat(self._idle.sample(time.monotonic()))
            except ConnectionError:
                # 刚好断开：线路组会去重连
                continue

    async def _server_time_loop(self) -> None:
        while True:
            await asyncio.sleep(SERVER_TIME_INTERVAL)
            socket = self.main_socket
            if socket is None or not socket.is_open() or self.state is not ConnState.ONLINE:
                continue
            try:
                await socket.send(".lq.Lobby.fetchServerTime", liblq.ReqCommon())
            except ConnectionError:
                continue

    async def _shutdown(self) -> None:
        """关掉线路组和后台任务，准备重新登录；不动正在跑的通知处理。"""
        self._stop_loops()
        if self._group is not None:
            group, self._group = self._group, None
            await group.close()

    async def close(self) -> None:
        """从账号池移除前调用：停掉一切，不再重连。"""
        self.state = ConnState.OFFLINE
        await self._shutdown()
        current = asyncio.current_task()
        tasks: list[asyncio.Task[None]] = [*self._notify_tasks, *(task for task, _ in self._online_pending.values())]
        for task in tasks:
            if task is not current:
                task.cancel()
        for head in self._game_heads.values():
            head.cancel()

    async def _fail(self, reason: str) -> None:
        self.state = ConnState.FAILED
        self.last_error = reason
        await self._shutdown()

    async def _give_up(self, reason: str) -> None:
        await self._fail(reason)
        await self.send_meta(f"账号 {self.label} {reason}，推送已停止。\n发送[雀魂重启服务]重新登录")

    def on_reconnect_started(self) -> None:
        if self.state is not ConnState.ONLINE:
            return
        logger.warning(f"[majs] {self.label} 断线，线路组开始重连")
        self.state = ConnState.RECONNECTING
        self._stop_loops()

    async def on_reconnected(self, changed: bool) -> None:
        try:
            await self._on_reconnected(changed)
        except Exception as e:
            # 意料之外的异常别让状态卡在「重连中」：先放弃并提醒，再原样抛出去
            await self._give_up(f"重连时出错：{e!r}")
            raise

    async def _on_reconnected(self, changed: bool) -> None:
        if not changed:
            # LobbyNetMgr：原线路重连上了就 LoginMgr.Relogin，带 reconnect=true；失败官方弹致命错误
            try:
                resp = await self._send_login(".lq.Lobby.oauth2Login", self._oauth2_login_req(self.access_token, True))
            except MajsoulLoginError as e:
                await self._give_up(str(e))
                return
            except NETWORK_ERRORS as e:
                await self._give_up(f"自动重连登录失败：{e!r}")
                return
            if resp.error.code or not resp.account_id:
                await self._give_up(f"自动重连登录失败（错误码 {resp.error.code}）")
                return
            # LoginMgr._OnReloginSuccess：OnLoginSuccess（发 loginBeat）→ fetchServerTime → loginSuccess
            await self._apply_login(resp)
            await self.login_beat()
            socket = self.main_socket
            if socket is not None:
                await socket.send(".lq.Lobby.fetchServerTime", liblq.ReqCommon())
            await self._send_login_success()
        main = self.main_socket
        if main is not None:
            self._preferred_route = main.route.id
        # 换线路走的是 fastLogin（LoginMgr.OnFastLogin），不用重新登录
        # 插件自己的：官方不重新拉好友，这里要拿断线期间变化后的好友状态
        await self.fetchInfo()
        if self.state is ConnState.RECONNECTING:
            self.state = ConnState.ONLINE
            self._start_loops()
        logger.info(f"[majs] {self.label} {'切换线路' if changed else '重连'}成功")

    async def on_reconnect_failed(self, reason: str) -> None:
        await self._give_up(reason)

    def prepare_login_request(self) -> liblq.ReqPrepareLogin:
        # NetRouteGroup:_StandbyPrepareLogin：用 LoginMgr 里的 access_token 和登录类型
        return liblq.ReqPrepareLogin(access_token=self.access_token, type=self.credential.login_type)

    async def _on_kicked(self, reason: str) -> None:
        # UI_AnotherLogin / UI_Hanguplogout 先关掉连接；官方之后关自动登录，这里自动重登一次
        who = self.label
        logger.warning(f"[majs] 账号 {who} {reason}")
        await self._shutdown()
        if self._kick_relogin_used:
            self.state = ConnState.KICKED
            self.last_error = reason
            await self.send_meta(
                f"账号 {who} 又{reason}，不再自动重新登录。\n"
                "反复出现说明还有别的程序在登录这个账号，处理后发送[雀魂重启服务]"
            )
            return
        self._kick_relogin_used = True
        await self.send_meta(f"账号 {who} {reason}，自动重新登录一次")
        try:
            await self.login()
        except MajsoulLoginError as e:
            await self.send_meta(f"账号 {who} 自动重新登录失败：{e}")

    # ---------- 通知 ----------

    def on_group_notify(self, socket: RouteSocket, notify: MajsoulDecodedMessage) -> None:
        if isinstance(notify.payload, (liblq.NotifyAnotherLogin, liblq.NotifyAccountLogout)):
            # 同一条连接上的第二条顶号/登出通知、旧连接迟到的通知，都算同一次被顶
            if socket is not self.main_socket or socket is self._kicked_socket:
                return
            self._kicked_socket = socket
            # 官方收到就立刻 LobbyNetMgr.Close()；先停掉线路组，接着到来的断开就不会被当成掉线去重连
            self.state = ConnState.OFFLINE
            if self._group is not None:
                self._group.mark_closed()
        task = spawn(self.handle_notify(notify), f"majs-notify-{notify.method_name}")
        self._notify_tasks.add(task)
        task.add_done_callback(self._notify_tasks.discard)

    async def handle_notify(self, notify: MajsoulDecodedMessage):
        if notify.method_name in IGNORED_NOTIFY:
            return
        payload = notify.payload
        if isinstance(payload, liblq.NotifyLoginQueueFinished):
            self._queue_finished.set()
            return
        logger.info(f"[majs] 通知: {notify}")
        if isinstance(payload, liblq.NotifyFriendStateChange):
            await self.handle_FriendStateChange(notify)
        elif isinstance(payload, liblq.NotifyFriendViewChange):
            await self.handle_FriendViewChange(notify)
        elif isinstance(payload, liblq.NotifyNewFriendApply):
            await self.handle_NewFriendApply(notify)
        elif isinstance(payload, liblq.NotifyFriendChange):
            await self.handle_FriendChange(notify)
        elif isinstance(payload, liblq.NotifyAnotherLogin):
            await self._on_kicked("在别处登录")
        elif isinstance(payload, liblq.NotifyAccountLogout):
            await self._on_kicked("被服务端登出")
        else:
            logger.warning(f"[majs] 未知通知: {notify}")

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

    async def send_meta(self, meta_msg: str):
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

        head = await self.fetch_game_head(game_uuid)
        if head is None:
            if account_id not in self.friends:
                return
            # 中途掉线也会清空 playing：人还离线或已回到这局，就是还没打完
            current = self.friends[account_id]
            if not current.is_online or current.playing.game_uuid == game_uuid:
                return
            await self.send_msg_to_user(str(account_id), f"{msg}获取对局结果失败\n对局牌谱:{url}")
            return

        players = head.result.players
        friend_seat, level_id, level_score = 0, 0, 0
        for account in head.accounts:
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

    async def record_call(self, method_name: str, req: betterproto.Message, resp_type: type[RespT]) -> RespT:
        async with self._record_lock:
            wait = self._last_record_fetch + RECORD_FETCH_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await self.rpc_call(method_name, req, resp_type)
            self._last_record_fetch = time.monotonic()
            return resp

    async def fetch_game_head(self, game_uuid: str) -> liblq.RecordGame | None:
        if game_uuid not in self._game_heads:
            self._game_heads[game_uuid] = asyncio.create_task(self.query_game_head(game_uuid))
            loop = asyncio.get_running_loop()
            loop.call_later(GAME_HEAD_CACHE_SECONDS, self._game_heads.pop, game_uuid, None)
        # shield：一个好友的处理被取消时，别把其他人共用的查询一起取消
        return await asyncio.shield(self._game_heads[game_uuid])

    async def query_game_head(self, game_uuid: str) -> liblq.RecordGame | None:
        # 推送只要名次和点数，用牌谱屋同款的 fetchGameRecordsDetail，不拉整局牌谱
        for delay in GAME_RECORD_RETRY_DELAYS:
            await asyncio.sleep(delay)
            resp = await self.record_call(
                ".lq.Lobby.fetchGameRecordsDetail",
                liblq.ReqGameRecordsDetail(uuid_list=[game_uuid]),
                liblq.ResGameRecordsDetail,
            )
            if resp.error.code:
                logger.warning(f"[majs] 获取对局结果 {game_uuid} 失败: {resp.error}")
                if resp.error.code == ERR_RECORD_FETCH_LIMIT:
                    return None
                continue
            for record in resp.record_list:
                if record.uuid == game_uuid:
                    return record
            logger.info(f"[majs] 对局结果 {game_uuid} 还没生成")
        logger.error(f"[majs] 获取对局结果 {game_uuid} 重试 {len(GAME_RECORD_RETRY_DELAYS)} 次仍失败")
        return None

    async def download_record_data(self, data_url: str) -> bytes:
        if not data_url:
            return b""
        # 客户端 UI_PaiPuNew.ReplaceUrl：不是 http 开头就拼上 prefix_url
        if not data_url.startswith("http"):
            if not self._region.record_prefix_url:
                logger.warning(f"[majs] {self._region.tag} 服没有牌谱前缀，无法下载 {data_url}")
                return b""
            data_url = self._region.record_prefix_url + data_url
        try:
            async with httpx2.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
                resp = await client.get(data_url)
        except httpx2.HTTPError as e:
            logger.warning(f"[majs] 下载牌谱数据失败 {data_url}: {e!r}")
            return b""
        if resp.status_code != 200:
            logger.warning(f"[majs] 下载牌谱数据失败 {data_url}: HTTP {resp.status_code}")
            return b""
        return resp.content

    async def handle_FriendViewChange(self, notify: MajsoulDecodedMessage):
        data = notify.payload
        if isinstance(data, liblq.NotifyFriendViewChange) and data.target_id in self.friends:
            self.friends[data.target_id].change_base(data.base)

    async def handle_FriendChange(self, notify: MajsoulDecodedMessage):
        data = notify.payload
        if not isinstance(data, liblq.NotifyFriendChange):
            return
        account_id = data.account_id
        existed = account_id in self.friends
        meta_msg = ""
        if data.type == 1:
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
        data = notify.payload
        if not isinstance(data, liblq.NotifyNewFriendApply):
            return
        account_id = data.account_id
        if account_id not in self.friend_apply_list:
            self.friend_apply_list.append(account_id)
        nickname = await self.fetchAccountNickname(account_id)
        await self.send_meta(f"收到来自 {nickname or account_id} 的好友申请")

        if MAJS_CONFIG.get_config("MajsIsAutoApplyFriend").data:
            await self.acceptFriendApply(account_id)

    async def fetchLiveGames(self) -> list[liblq.GameLiveHead]:
        game_live_list: List[liblq.GameLiveHead] = []
        for gm in GameMode:
            games = await self.rpc_call(
                ".lq.Lobby.fetchGameLiveList",
                liblq.ReqGameLiveList(filter_id=int(f"2{gm.value}")),
                liblq.ResGameLiveList,
            )
            game_live_list.extend(games.live_list)
            await asyncio.sleep(0.7)
        return game_live_list

    async def fetchInfo(self) -> None:
        resp = await self.rpc_call(".lq.Lobby.fetchInfo", liblq.ReqCommon(), liblq.ResFetchInfo)
        # 服务端给的是全量列表，整体替换；出错时保留旧数据，别清空
        if resp.error.code or resp.friend_list.error.code:
            logger.warning(f"[majs] 刷新好友列表失败, 保留旧数据: {resp.error} {resp.friend_list.error}")
        else:
            self.friends = {f.base.account_id: MajsoulFriend(f) for f in resp.friend_list.friends}
        if not resp.error.code and not resp.friend_apply_list.error.code:
            self.friend_apply_list = list(dict.fromkeys(a.account_id for a in resp.friend_apply_list.applies))

    async def fetchAccountNickname(self, account_id: int) -> str | None:
        resp = await self.rpc_call(
            ".lq.Lobby.fetchMultiAccountBrief",
            liblq.ReqMultiAccountId(account_id_list=[account_id]),
            liblq.ResMultiAccountBrief,
        )
        if resp.error.code:
            return None
        for player in resp.players:
            if player.account_id == account_id and player.nickname:
                return player.nickname
        return None

    async def acceptFriendApply(self, account_id: int) -> liblq.ResCommon:
        resp = await self.rpc_call(
            ".lq.Lobby.handleFriendApply",
            liblq.ReqHandleFriendApply(method=1, target_id=account_id),
            liblq.ResCommon,
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

        logs = await self.record_call(
            ".lq.Lobby.fetchGameRecord",
            liblq.ReqGameRecord(game_uuid=log_id, client_version_string=self.clientVersionString),
            liblq.ResGameRecord,
        )
        if logs.error.code:
            logger.warning(f"[majs] 获取牌谱 {log_id} 失败: {logs.error}")
            return record_error_text(logs.error.code)
        # 客户端拉到牌谱后都会补一句 readGameRecord（GameMgr:CheckPaiPu）
        await self.rpc_call(
            ".lq.Lobby.readGameRecord",
            liblq.ReqGameRecord(client_version_string=self.clientVersionString),
            liblq.ResCommon,
        )
        record_data = logs.data or await self.download_record_data(logs.data_url)
        if not record_data:
            return "牌谱数据下载失败，请稍后重试"
        detail_records = liblq.Wrapper().parse(record_data)

        payload = liblq.GameDetailRecords().parse(detail_records.data)

        raw_actions = (
            list(payload.records)
            if payload.version < 210715 and len(payload.records) > 0
            else [action.result for action in payload.actions if action.result]
        )
        action_list: list[MjsLogItem] = []
        for value in raw_actions:
            raw = liblq.Wrapper().parse(value)
            action_type = message_type(raw.name)
            if action_type is None:
                logger.warning(f"[majs] 牌谱里有本地 lib/lq 没有的动作 {raw.name}，跳过")
                continue
            action_list.append(MjsLogItem(name=action_type.__name__, data=action_type().parse(raw.data)))

        tenhou_log = MajsoulPaipuParser().handle_game_record(record=MjsLog(logs.head, action_list))

        tenhou_log["head"] = process_dict(logs.head.__dict__)
        tenhou_log["game_id"] = game_id
        tenhou_log["log_id"] = log_id
        tenhou_log["target_id"] = target_id
        tenhou_log["_parser_version"] = PAIPU_CACHE_VERSION

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


def connection_from_user(user: MajsUser) -> MajsoulConnection | None:
    if user.login_type == SOCIO_TYPE_YOSTAR:
        # passport uid 存在 account；旧数据只有雀魂 account_id，重新登录一次即可
        passport_uid = user.account or user.uid
        if not passport_uid:
            logger.warning(f"[majs] Yostar 账号 {user.uid} 缺少 passport uid，请重新[雀魂登录日服]")
            return None
        credential = MajsoulCredential(
            login_type=SOCIO_TYPE_YOSTAR,
            access_token=user.cookie or "",
            passport_uid=passport_uid,
            passport_token=user.token,
            lang=user.lang,
        )
    else:
        credential = MajsoulCredential(
            login_type=SOCIO_TYPE_CN,
            username=user.username,
            password=user.password,
            access_token=user.cookie or "",
        )
    return MajsoulConnection(credential, make_device_id(credential.region, credential.identity))


class MajsoulManager:
    def __init__(self):
        self.conn: list[MajsoulConnection] = []
        self._loaded = False
        # 两处同时登录会把同一个号登两次，先登的被顶掉
        self._lock = asyncio.Lock()

    async def _load(self) -> None:
        if self._loaded:
            return
        users = await MajsUser.get_all_user()
        self.conn = [conn for conn in map(connection_from_user, users) if conn is not None]
        self._loaded = True
        logger.info(f"[majs] 账号池共 {len(self.conn)} 个账号")

    async def start(self) -> list[str]:
        """把账号池里没在线的号都登录上，返回每个号的状态。"""
        async with self._lock:
            return await self._start()

    async def restart(self) -> list[str]:
        async with self._lock:
            for conn in self.conn:
                await conn.close()
            self.conn = []
            self._loaded = False
            return await self._start()

    async def _start(self) -> list[str]:
        await self._load()
        if not self.conn:
            return ["账号池里还没有账号，请先用[雀魂登陆国服]或[雀魂登录日服]添加"]
        for conn in self.conn:
            if conn.state not in (ConnState.ONLINE, ConnState.RECONNECTING):
                await self._login_one(conn)
        return [conn.status_line() for conn in self.conn]

    async def _login_one(self, conn: MajsoulConnection) -> None:
        # 手动启动相当于玩家重新打开游戏，被顶号后的自动重登机会也重新算
        conn.reset_kick()
        try:
            await conn.login()
        except MajsoulLoginError as e:
            logger.warning(f"[majs] 账号 {conn.label} 登录失败: {e}")

    async def add_account(self, credential: MajsoulCredential) -> MajsoulConnection:
        """登录一个新号并放进账号池，失败抛 MajsoulLoginError。"""
        async with self._lock:
            await self._load()
            # 同一个号的旧会话先关掉：新登录会把它顶掉，它又会自动重登把新的顶回去
            replaced = await self._drop(lambda conn: conn.credential.same_account(credential))
            conn = MajsoulConnection(credential, make_device_id(credential.region, credential.identity))
            try:
                await conn.login()
            except MajsoulLoginError:
                # 比如密码输错：把旧会话放回去重新登录，别让这个号从账号池里消失
                for old in replaced:
                    self.conn.append(old)
                    await self._login_one(old)
                raise
            await self._drop(lambda old: old.account_id == conn.account_id)
            self.conn.append(conn)
            return conn

    async def _drop(self, match: Callable[[MajsoulConnection], bool]) -> list[MajsoulConnection]:
        dropped = [conn for conn in self.conn if match(conn)]
        for old in dropped:
            await old.close()
            self.conn.remove(old)
        return dropped

    def get_conn(self) -> MajsoulConnection | None:
        # 断开的连接留在池里等重启，它的好友缓存不能再拿来回答
        for conn in self.conn:
            if not conn.is_closed():
                return conn
        return None

    def get_all_conn(self) -> list[MajsoulConnection]:
        return self.conn


manager = MajsoulManager()
