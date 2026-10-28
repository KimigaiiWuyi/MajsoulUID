"""主 / 备用 / 探测三条线路，对应客户端 NetRouteGroup.lua 和 GatewayFetcher.lua。

- 主线路断开：在同一条线路上重连；重连超过 20 秒、或主线路成了黑洞，就切到备用线路
- 一直挑一条线路当备用，连上后 prepareLogin；备用线路不够好时再开探测线路，好得多就替换掉
"""

import time
import random
import asyncio
from typing import Callable, Protocol, Coroutine

import httpx2
import msgspec
import betterproto
from msgspec.structs import replace
from websockets.exceptions import InvalidHandshake

from gsuid_core.logger import logger

from ..lib import lq as liblq
from .codec import CLIENT_VERSION
from .model import MajsoulRoute, MajsoulRoutesData, MajsoulRoutesResp, MajsoulDecodedMessage
from .route import NEVER, RespT, RouteInfo, RouteSocket, RouteRejected, spawn
from .constants import (
    BUSY_IDLE,
    DELAY_INF,
    USER_AGENT,
    BAN_REPLACED,
    BUSY_REMOVED,
    BUSY_REJECTED,
    GATEWAY_BATCH,
    BAN_DETECT_BAD,
    DETAIL_PLATFORM,
    GATEWAY_TIMEOUT,
    ROUTE_TYPE_MAIN,
    ROUTE_TYPE_NONE,
    SWITCH_COOLDOWN,
    BAN_DETECT_WORSE,
    RECONNECT_DELAYS,
    BAN_SWITCH_FAILED,
    ROUTE_TYPE_DETECT,
    BAN_CONNECT_FAILED,
    BAN_PREPARE_FAILED,
    BLACK_HOLE_QUALITY,
    BLACK_HOLE_SECONDS,
    ROUTE_TYPE_STANDBY,
    SWITCH_MAIN_MARGIN,
    DELAY_BAD_THRESHOLD,
    DETECT_COOLDOWN_MAX,
    DETECT_COOLDOWN_MIN,
    GATEWAY_RETRY_DELAY,
    GROUP_TICK_INTERVAL,
    MAIN_SETTLE_SECONDS,
    DELAY_GOOD_THRESHOLD,
    SWITCH_STANDBY_MARGIN,
    RECONNECT_SWITCH_AFTER,
    STANDBY_SETTLE_SECONDS,
    GATEWAY_REFRESH_INTERVAL,
    GATEWAY_MAINTENANCE_REFRESH,
    MajsoulRegion,
)

# 连线路时可能遇到的网络错误（TimeoutError、ConnectionError 都是 OSError）
SOCKET_ERRORS = (OSError, InvalidHandshake)


class GroupListener(Protocol):
    """NetRouteGroup 往外抛的事件，由账号会话（LobbyNetMgr / LoginMgr 那一层）处理。"""

    def on_group_notify(self, socket: RouteSocket, notify: MajsoulDecodedMessage) -> None: ...

    def on_reconnect_started(self) -> None: ...

    async def on_reconnected(self, changed: bool) -> None: ...

    async def on_reconnect_failed(self, reason: str) -> None: ...

    def prepare_login_request(self) -> liblq.ReqPrepareLogin: ...


class GatewayFetcher:
    """GatewayFetcher.lua：向入口网关要线路表，一个账号会话一份。"""

    def __init__(self, region: MajsoulRegion) -> None:
        self._region = region
        self.routes: dict[str, MajsoulRoute] = {}
        self.maintenance = ""
        self.on_routes: Callable[[], None] | None = None
        self._ok = False
        self._fails = 0
        self._fetched_at = NEVER
        self._preferred = ""
        self._task: asyncio.Task[None] | None = None

    def fetching(self) -> bool:
        return self._task is not None and not self._task.done()

    def check_fetch(self, force: bool = False) -> bool:
        """GatewayFetcher.CheckFetch：到点了就在后台刷新，返回 True 表示正在刷新。"""
        if self.fetching():
            return True
        interval = GATEWAY_REFRESH_INTERVAL if self._ok else 1 + self._fails * 2
        if self._fails > 10:
            interval = 20
        if self.maintenance:
            interval = min(interval, GATEWAY_MAINTENANCE_REFRESH)
        if force or time.monotonic() > self._fetched_at + interval:
            self._task = spawn(self._fetch_in_background(), f"majs-gateway-{self._region.tag}")
            return True
        return False

    async def _fetch_in_background(self) -> None:
        if await self.fetch() and self.on_routes is not None:
            self.on_routes()

    async def fetch(self) -> bool:
        """先问上次成功的网关，3 秒没结果再每批 3 个问其他的，谁先回用谁。"""
        gateways = list(self._region.gateways)
        if self._preferred in gateways:
            start = gateways.index(self._preferred)
            delay = GATEWAY_RETRY_DELAY
        else:
            start = random.randrange(len(gateways))
            delay = 0.1
        queue = [gateways[(start + i) % len(gateways)] for i in range(len(gateways))]
        pending: set[asyncio.Task[tuple[str, MajsoulRoutesData] | None]] = set()
        if self._preferred in gateways:
            pending.add(asyncio.create_task(self._try_gateway(queue.pop(0))))
        next_batch = time.monotonic() + delay
        try:
            while pending or queue:
                timeout = max(0.0, next_batch - time.monotonic()) if queue else None
                if pending:
                    done, pending = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        result = task.result()
                        if result is not None:
                            self._apply(*result)
                            return True
                elif timeout:
                    await asyncio.sleep(timeout)
                if queue and time.monotonic() >= next_batch:
                    for gateway in queue[:GATEWAY_BATCH]:
                        pending.add(asyncio.create_task(self._try_gateway(gateway)))
                    queue = queue[GATEWAY_BATCH:]
                    next_batch = time.monotonic() + delay
        finally:
            for task in pending:
                task.cancel()
        self._ok = False
        self._fails += 1
        self._fetched_at = time.monotonic()
        self.routes = {}
        logger.warning(f"[majs] {self._region.tag} 服的入口网关全部不可用（第 {self._fails} 次）")
        return False

    async def _try_gateway(self, gateway: str) -> tuple[str, MajsoulRoutesData] | None:
        params = {
            "platform": DETAIL_PLATFORM,
            "version": CLIENT_VERSION.package_version,
            "lang": self._region.route_lang,
        }
        headers = {"User-Agent": USER_AGENT, "Referer": self._region.web_url}
        try:
            async with httpx2.AsyncClient(headers=headers, timeout=GATEWAY_TIMEOUT) as client:
                resp = await client.get(f"{gateway}/api/clientgate/routes", params=params)
                resp.raise_for_status()
        except httpx2.HTTPError as e:
            logger.warning(f"[majs] 入口网关 {gateway} 不可用: {e!r}")
            return None
        try:
            data = msgspec.json.decode(resp.content, type=MajsoulRoutesResp).data
        except msgspec.DecodeError as e:
            logger.warning(f"[majs] 入口网关 {gateway} 返回内容无法解析: {e}")
            return None
        if not data.routes:
            logger.warning(f"[majs] 入口网关 {gateway} 没有返回可用路由")
            return None
        return gateway, data

    def _apply(self, gateway: str, data: MajsoulRoutesData) -> None:
        self._ok = True
        self._fails = 0
        self._fetched_at = time.monotonic()
        self._preferred = gateway
        notes = [m.content for m in data.maintenance if m.lang in (self._region.route_lang, "chs")]
        self.maintenance = "\n".join(notes or [m.content for m in data.maintenance[:1]])
        # 不在新表里的线路标成 removed，之后不再拿来用
        routes = {rid: replace(route, state=BUSY_REMOVED) for rid, route in self.routes.items()}
        routes.update({route.id: route for route in data.routes})
        self.routes = routes


class RouteGroup:
    def __init__(self, region: MajsoulRegion, gateway: GatewayFetcher, listener: GroupListener) -> None:
        self._region = region
        self._gateway = gateway
        self._listener = listener
        self.routes: dict[str, RouteInfo] = {}
        self.main: RouteSocket | None = None
        self.standby: RouteSocket | None = None
        self.detect: RouteSocket | None = None
        # 登录成功、start() 之后才管重连和备用线路（InitFromEntrance）
        self.closed = True
        self.reconnecting = False
        self._reconnect_started = 0.0
        self._last_reconnect = NEVER
        self._black_hole_since: float | None = None
        self._last_detect = NEVER
        self._tick_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._jobs: set[asyncio.Task[None]] = set()

    # ---------- 对外 ----------

    def sync_routes(self) -> None:
        """NetRouteGroup:_OnGatewayInfo：新线路加进来，旧线路更新状态，不能用的非主线路关掉。"""
        for rid, route in self._gateway.routes.items():
            if rid not in self.routes:
                self.routes[rid] = RouteInfo(route=route, busy_state=route.state)
                continue
            info = self.routes[rid]
            info.route, info.busy_state = route, route.state
            if info.can_use():
                continue
            for socket in (self.standby, self.detect):
                if socket is not None and socket.info is info:
                    self._forget(socket)
                    self._run(socket.close(), f"majs-drop-{rid}")

    async def open_entrance(self, preferred_route: str) -> RouteSocket:
        """NetRouteGroup_Entrance:_OnFirstConnect：上次用的线路能用就用，否则在 level 最高的里随机挑。"""
        self.sync_routes()
        usable = [info for info in self.routes.values() if info.can_use()]
        if preferred_route in self.routes and self.routes[preferred_route].can_use():
            info = self.routes[preferred_route]
        elif usable:
            top = max(info.route.level for info in usable)
            info = random.choice([info for info in usable if info.route.level == top])
        else:
            raise ConnectionError(f"[majs] {self._region.tag} 服没有可用线路")
        socket = self._new_socket(info, ROUTE_TYPE_MAIN)
        self.main = socket
        try:
            await socket.open()
        except RouteRejected:
            info.busy_state = BUSY_REJECTED
            self._gateway.check_fetch()
            raise
        return socket

    def start(self) -> None:
        self.closed = False
        self._gateway.on_routes = self.sync_routes
        self._tick_task = spawn(self._tick_loop(), f"majs-group-{self._region.tag}")

    def mark_closed(self) -> None:
        """立刻停止重连和切换（被顶号时官方同步调 LobbyNetMgr.Close），连接稍后再关。"""
        self.closed = True

    async def close(self) -> None:
        self.closed = True
        self.reconnecting = False
        self._gateway.on_routes = None
        current = asyncio.current_task()
        tasks = [task for task in (self._tick_task, self._reconnect_task, *self._jobs) if task is not None]
        for task in tasks:
            if task is not current:
                task.cancel()
        sockets = [socket for socket in (self.main, self.standby, self.detect) if socket is not None]
        self.main = self.standby = self.detect = None
        for socket in sockets:
            await socket.close()

    async def request(self, method_name: str, req: betterproto.Message, resp_type: type[RespT]) -> RespT:
        if self.main is None or self.reconnecting:
            raise ConnectionError("[majs] 主线路还没连上")
        return await self.main.request(method_name, req, resp_type)

    # ---------- 线路事件 ----------

    def _new_socket(self, info: RouteInfo, route_type: int) -> RouteSocket:
        return RouteSocket(info, self._region, route_type, self._on_notify, self._on_close)

    def _on_notify(self, socket: RouteSocket, notify: MajsoulDecodedMessage) -> None:
        # NetRouteGroup:_OnRouteNotifyProto：只处理主线路上的通知
        if socket is not self.main:
            logger.debug(f"[majs] 非主线路 {socket.route.id} 的通知 {notify.method_name}，忽略")
            return
        self._listener.on_group_notify(socket, notify)

    def _on_close(self, socket: RouteSocket) -> None:
        # NetRouteGroup:_OnRouteClose：主线路意外断开就在同一条线路上重连
        if socket is self.main:
            if self.closed:
                self.main = None
            elif not self.reconnecting:
                logger.warning(f"[majs] 主线路 {socket.route.id} 断开，开始重连")
                self.reconnecting = True
                self._reconnect_started = time.monotonic()
                self._listener.on_reconnect_started()
                self._reconnect_task = spawn(self._reconnect_loop(socket.info), "majs-reconnect")
        elif socket is self.standby:
            self.standby = None
        elif socket is self.detect:
            self.detect = None

    def _forget(self, socket: RouteSocket) -> None:
        if socket is self.standby:
            self.standby = None
        elif socket is self.detect:
            self.detect = None

    def _run(self, coro: Coroutine[None, None, None], name: str) -> None:
        task = spawn(coro, name)
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    # ---------- 重连 ----------

    async def _reconnect_loop(self, info: RouteInfo) -> None:
        for attempt, delay in enumerate(RECONNECT_DELAYS, 1):
            await asyncio.sleep(delay)
            if self.closed or not self.reconnecting:
                return
            logger.info(f"[majs] 主线路 {info.route.id} 第 {attempt} 次重连")
            socket = self._new_socket(info, ROUTE_TYPE_MAIN)
            self.main = socket
            try:
                await socket.open()
            except RouteRejected:
                info.busy_state = BUSY_REJECTED
                self._gateway.check_fetch()
                await socket.close()
                continue
            except SOCKET_ERRORS as e:
                logger.warning(f"[majs] 主线路 {info.route.id} 第 {attempt} 次重连失败: {e!r}")
                if isinstance(e, TimeoutError):
                    # NetRoute:Connect 10 秒没连上：关掉并 5 分钟内不再拿来探测
                    info.ban(BAN_CONNECT_FAILED)
                await socket.close()
                continue
            # NetRouteGroup:_OnRouteUseable → _OnReconnectSuccess(main, changed=false)
            self._reconnect_success()
            await self._listener.on_reconnected(False)
            return
        await self._fail(f"断线后重连 {len(RECONNECT_DELAYS)} 次都没连上")

    def _reconnect_success(self) -> None:
        self.reconnecting = False
        self._black_hole_since = None
        self._last_reconnect = time.monotonic()

    async def _fail(self, reason: str) -> None:
        await self.close()
        await self._listener.on_reconnect_failed(reason)

    # ---------- 每秒检查 ----------

    async def _tick_loop(self) -> None:
        while not self.closed:
            await asyncio.sleep(GROUP_TICK_INTERVAL)
            if self.closed:
                return
            now = time.monotonic()
            # NeedOpenDetect() 恒为 false：主线路只做黑洞检查
            self._tick_black_hole(now)
            self._tick_detect(now)

    def _tick_black_hole(self, now: float) -> None:
        main = self.main
        quality = DELAY_INF
        if main is not None:
            quality = main.quality()
            if main.locked or main.during_detect or now < main.opened_at + MAIN_SETTLE_SECONDS:
                return
        if self.reconnecting:
            self._black_hole_since = None
            if now - self._reconnect_started > RECONNECT_SWITCH_AFTER:
                self._tick_main_route(now)
        elif quality > BLACK_HOLE_QUALITY:
            if self._black_hole_since is None:
                self._black_hole_since = now
            elif now - self._black_hole_since > BLACK_HOLE_SECONDS:
                self._tick_main_route(now)
        else:
            self._black_hole_since = None

    def _tick_main_route(self, now: float) -> None:
        """NetRouteGroup:_TickCheckMainRoute：备用线路明显更好就切过去。"""
        if now < self._last_reconnect + SWITCH_COOLDOWN:
            return
        main, standby = self.main, self.standby
        quality = DELAY_INF
        if main is not None:
            quality = main.quality()
            settling = now < main.opened_at + MAIN_SETTLE_SECONDS
            if main.locked or main.during_detect or settling or main.waiting_rpc_count > 0:
                return
        if standby is None or not standby.usable or not standby.is_open():
            return
        standby_quality = standby.quality()
        if standby.locked or standby.during_detect or now < standby.opened_at + STANDBY_SETTLE_SECONDS:
            return
        if standby_quality > DELAY_BAD_THRESHOLD or standby_quality + SWITCH_MAIN_MARGIN >= quality:
            # 只是稍好一点时官方弹按钮让玩家手动切，这里没人点，不切
            return
        if standby.need_detect():
            standby.detect()
            return
        standby.locked = True
        if main is not None:
            main.locked = True
        self._run(self._switch_standby_to_main(), "majs-switch-main")

    def _tick_detect(self, now: float) -> None:
        """NetRouteGroup:_TickCheckDetect：维护备用线路，备用不够好时开探测线路比一比。"""
        standby, detect = self.standby, self.detect
        standby_quality = DELAY_INF
        if standby is not None:
            standby_quality = standby.quality()
            if standby.locked or standby.during_detect or now < standby.opened_at + STANDBY_SETTLE_SECONDS:
                return
        if detect is None:
            if self._should_detect(now, standby_quality):
                self._start_detect(now, standby_quality)
            return
        if not detect.usable or detect.locked or detect.during_detect:
            return
        if now < detect.opened_at + STANDBY_SETTLE_SECONDS:
            return
        detect_quality = detect.quality()
        if detect_quality + SWITCH_STANDBY_MARGIN < standby_quality:
            detect.locked = True
            if standby is not None:
                standby.locked = True
            self._run(self._switch_detect_to_standby(), "majs-switch-standby")
            return
        detect.info.ban(BAN_DETECT_BAD if detect_quality > DELAY_BAD_THRESHOLD else BAN_DETECT_WORSE)
        self.detect = None
        self._run(detect.close(), f"majs-drop-{detect.route.id}")

    def _detect_cooldown(self, standby_quality: float) -> float:
        floor, ceil = DELAY_GOOD_THRESHOLD - 0.1, DELAY_BAD_THRESHOLD
        if standby_quality > ceil:
            return DETECT_COOLDOWN_MIN
        ratio = (standby_quality - floor) / (ceil - floor)
        return DETECT_COOLDOWN_MAX * (1 - ratio) + DETECT_COOLDOWN_MIN * ratio

    def _should_detect(self, now: float, standby_quality: float) -> bool:
        if standby_quality <= DELAY_GOOD_THRESHOLD - 0.05:
            return False
        if now - self._last_detect < self._detect_cooldown(standby_quality):
            return False
        # 线路表该刷新了就先刷新，下一轮再探测
        return not self._gateway.check_fetch()

    def _can_detect(self, info: RouteInfo, now: float) -> bool:
        in_use = {socket.info.route.id for socket in (self.main, self.standby, self.detect) if socket is not None}
        return info.route.id not in in_use and info.busy_state == BUSY_IDLE and now >= info.banned_until

    def _start_detect(self, now: float, standby_quality: float) -> None:
        """NetRouteGroup:_DoDetect：没连过、或上次测得比备用好的线路优先，随机挑一条连上。"""
        better: list[RouteInfo] = []
        worse: list[RouteInfo] = []
        for info in self.routes.values():
            if self._can_detect(info, now):
                (better if not info.connected or info.last_quality < standby_quality else worse).append(info)
        candidates = better or worse
        if not candidates:
            return
        self._last_detect = now
        info = random.choice(candidates)
        # _OnRouteOpen：还没有备用线路就当备用，否则当探测线路
        if self.standby is None:
            socket = self._new_socket(info, ROUTE_TYPE_STANDBY)
            socket.locked = True
            self.standby = socket
        else:
            socket = self._new_socket(info, ROUTE_TYPE_DETECT)
            self.detect = socket
        logger.info(f"[majs] 探测线路 {info.route.id}（类型 {socket.route_type}）")
        self._run(self._open_side_route(socket), f"majs-detect-{info.route.id}")

    async def _open_side_route(self, socket: RouteSocket) -> None:
        try:
            await socket.open()
        except RouteRejected:
            socket.info.busy_state = BUSY_REJECTED
            self._gateway.check_fetch()
            self._forget(socket)
            await socket.close()
            return
        except SOCKET_ERRORS as e:
            logger.info(f"[majs] 线路 {socket.route.id} 连不上: {e!r}")
            socket.info.ban(BAN_CONNECT_FAILED)
            self._forget(socket)
            await socket.close()
            return
        if self.closed or (socket is not self.standby and socket is not self.detect):
            await socket.close()
            return
        # NetRouteGroup:_OnRouteUseable：备用线路预登录并测速，探测线路只测速
        socket.detect()
        if socket is self.standby:
            await self._prepare_standby(socket)

    async def _prepare_standby(self, socket: RouteSocket) -> None:
        # NetRouteGroup:_StandbyPrepareLogin：用当前 access_token 在备用线路上预登录
        socket.locked = True
        try:
            resp = await socket.request(
                ".lq.Lobby.prepareLogin", self._listener.prepare_login_request(), liblq.ResCommon
            )
            ok = not resp.error.code
        except SOCKET_ERRORS as e:
            logger.info(f"[majs] 备用线路 {socket.route.id} 预登录失败: {e!r}")
            ok = False
        socket.locked = False
        if not ok:
            socket.info.ban(BAN_PREPARE_FAILED)
            self._forget(socket)
            await socket.close()

    # ---------- 切换 ----------

    async def _switch_route(self, old: RouteSocket | None, new: RouteSocket, route_type: int) -> bool:
        """NetRouteGroup:_SwitchRoute：在新线路上发 requestRouteChange，成功就顶替旧线路。"""
        before = old.route.id if old is not None else ""
        if old is not None:
            old.locked = True
        new.locked = True
        logger.info(f"[majs] 线路切换（类型 {route_type}）: {before or '无'} → {new.route.id}")
        try:
            resp = await new.request(
                ".lq.Route.requestRouteChange",
                liblq.ReqRequestRouteChange(before=before, route_id=new.route.id, type=route_type),
                liblq.ResRequestRouteChange,
            )
            ok = not resp.error.code and resp.result == 1
        except SOCKET_ERRORS as e:
            logger.warning(f"[majs] 线路切换失败: {e!r}")
            ok = False
        if not ok:
            if old is not None:
                old.locked = False
            new.locked = False
            new.info.ban(BAN_SWITCH_FAILED)
            self._forget(new)
            await new.close()
            return False
        new.route_type, new.locked = route_type, False
        if route_type == ROUTE_TYPE_MAIN:
            self.main, self.standby = new, None
        else:
            self.standby, self.detect = new, None
        if old is not None:
            old.locked, old.route_type = False, ROUTE_TYPE_NONE
            old.info.ban(BAN_REPLACED)
            await old.close()
        return True

    async def _switch_standby_to_main(self) -> None:
        standby = self.standby
        if standby is None:
            return
        was_reconnecting = self.reconnecting
        if was_reconnecting and self._reconnect_task is not None:
            # 插件自己的：先停掉原线路的重连，免得两边同时登录
            self._reconnect_task.cancel()
        if not await self._switch_route(self.main, standby, ROUTE_TYPE_MAIN):
            if was_reconnecting:
                await self._fail("断线后切换备用线路失败")
            return
        try:
            resp = await standby.request(".lq.Lobby.fastLogin", liblq.ReqFastLogin(), liblq.ResFastLogin)
            ok = not resp.error.code
        except SOCKET_ERRORS as e:
            logger.warning(f"[majs] 切换线路后快速登录失败: {e!r}")
            ok = False
        if not ok:
            # 插件自己的：官方这里会停在没有主线路的状态，机器人直接按断线处理
            await self._fail("切换线路后快速登录失败")
            return
        logger.info(f"[majs] 主线路已切到 {standby.route.id}")
        if was_reconnecting:
            self._reconnect_success()
        else:
            self._last_reconnect = time.monotonic()
        # _OnReconnectSuccess(main, changed=true)：换了线路，LobbyNetMgr 不再 Relogin
        await self._listener.on_reconnected(True)

    async def _switch_detect_to_standby(self) -> None:
        detect = self.detect
        if detect is None:
            return
        if await self._switch_route(self.standby, detect, ROUTE_TYPE_STANDBY):
            await self._prepare_standby(detect)
