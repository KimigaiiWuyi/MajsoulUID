"""一条到路由 /gateway 的 WebSocket，对应客户端的 NetRoute.lua。"""

import math
import time
import asyncio
from typing import TypeVar, Callable, Coroutine
from dataclasses import dataclass

import betterproto
from websockets.typing import Origin
from websockets.protocol import State
from websockets.exceptions import ConnectionClosed
from websockets.asyncio.client import ClientConnection, connect

from gsuid_core.logger import logger

from ..lib import lq as liblq
from .codec import RPC_MAP, MajsoulProtoCodec
from .model import MajsoulRoute, MajsoulDecodedMessage
from .constants import (
    BUSY_IDLE,
    DELAY_INF,
    USER_AGENT,
    BUSY_NORMAL,
    DETECT_MAX_AGE,
    CONNECT_TIMEOUT,
    DETAIL_PLATFORM,
    DETECT_INTERVAL,
    STANDBY_MAX_AGE,
    ROUTE_QUALITY_GAP,
    ROUTE_TYPE_DETECT,
    DETAIL_PLATFORM_ID,
    ROUTE_TYPE_STANDBY,
    HEARTBEAT_FIELD_MAX,
    ROUTE_PING_INTERVAL,
    ROUTE_PING_INTERVAL_UNSTABLE,
    MajsoulRegion,
)
from .route_delay import RouteDelayWatcher

RespT = TypeVar("RespT", bound=betterproto.Message)

HEARTBEAT = ".lq.Route.heartbeat"
CONNECTION_SHUTDOWN = ".lq.NotifyConnectionShutdown"
# RouteDelayWatcher / RequestClientHandle 都是 0.5 秒跑一轮
TICK_INTERVAL = 0.5
# NetRoute:Detect 连发 ping 的节奏：第一个 0.2 秒后，之后每 0.5 秒一个
DETECT_FIRST_PING = 0.2
DETECT_PING_GAP = 0.5
NEVER = -1e9


class MajsoulRpcTimeout(TimeoutError):
    pass


class RouteRejected(ConnectionError):
    """requestConnection 的 result 不是 1：这条线路拒绝了本次连接。"""


@dataclass
class RouteInfo:
    """一条线路跨多次连接都要记住的东西，对应 NetRoute 对象上常驻的字段。"""

    route: MajsoulRoute
    busy_state: str
    banned_until: float = 0.0
    # 本次运行里连过没有；没连过的线路优先拿来探测
    connected: bool = False
    last_quality: float = DELAY_INF
    detect_end: float = -1000.0

    def can_use(self) -> bool:
        return self.busy_state in (BUSY_IDLE, BUSY_NORMAL)

    def ban(self, seconds: float) -> None:
        self.banned_until = max(self.banned_until, time.monotonic() + seconds)


def log_task_error(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"[majs] 后台任务 {task.get_name()} 出错: {exc!r}", exc_info=exc)


def spawn(coro: Coroutine[None, None, None], name: str) -> asyncio.Task[None]:
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(log_task_error)
    return task


def heartbeat_ms(seconds: float) -> int:
    return max(0, min(math.floor(seconds * 1000), HEARTBEAT_FIELD_MAX))


class RouteSocket:
    def __init__(
        self,
        info: RouteInfo,
        region: MajsoulRegion,
        route_type: int,
        on_notify: Callable[["RouteSocket", MajsoulDecodedMessage], None],
        on_close: Callable[["RouteSocket"], None],
    ) -> None:
        self.info = info
        self.route = info.route
        self.route_type = route_type
        # 切换线路、备用线路预登录时锁住，其间不自动关、不参与切换判断
        self.locked = False
        self.during_detect = False
        self.opened_at = NEVER
        # requestConnection 通过后才算能用（EConnectState.usable）
        self.usable = False
        self._region = region
        self._endpoint = f"{'wss' if self.route.ssl else 'ws'}://{self.route.domain}/gateway"
        self._codec = MajsoulProtoCodec(RPC_MAP)
        self._on_notify = on_notify
        self._on_close = on_close
        self._ws: ClientConnection | None = None
        self._pending: dict[int, asyncio.Future[betterproto.Message]] = {}
        # 还在等回包的非心跳请求（NetRoute.waitingRpcCount）
        self._waiting_rpc: set[int] = set()
        # delayWatcher 只记心跳，delayWatcherAll 记全部请求并负责判超时
        self._heartbeat_delay = RouteDelayWatcher()
        self._all_delay = RouteDelayWatcher()
        self._last_ping = 0.0
        self._tasks: list[asyncio.Task[None]] = []
        self._expiring: asyncio.Task[None] | None = None
        self._closing = False
        self._closed = False

    @property
    def waiting_rpc_count(self) -> int:
        return len(self._waiting_rpc)

    def is_open(self) -> bool:
        return self._ws is not None and not self._closed and self._ws.state is State.OPEN

    def quality(self) -> float:
        """NetRoute:GetQualityGrade：没连着就是 DELAY_INF，顺手记下给探测挑线路用。"""
        if not self.is_open():
            return DELAY_INF
        self.info.last_quality = self._heartbeat_delay.quality
        return self.info.last_quality

    def delay(self) -> float:
        return self._heartbeat_delay.delay if self.is_open() else DELAY_INF

    async def open(self) -> None:
        logger.info(f"[majs] 连接路由 {self.route.id}（类型 {self.route_type}）: {self._endpoint}")
        self.info.connected = True
        # 客户端跑在浏览器里：带页面的 Origin 和 UA，不发 ping 帧，保活靠 Route.heartbeat
        self._ws = await connect(
            self._endpoint,
            origin=Origin(self._region.origin),
            user_agent_header=USER_AGENT,
            proxy=None,
            open_timeout=CONNECT_TIMEOUT,
            ping_interval=None,
        )
        if self._closing:
            # 握手期间已经被主动关闭：不再起收发循环，免得留下没人管的连接
            await self._ws.close()
            raise ConnectionError(f"[majs] 路由 {self.route.id} 在连接时被关闭")
        self.opened_at = time.monotonic()
        self._tasks = [
            spawn(self._recv_loop(), f"majs-recv-{self.route.id}"),
            spawn(self._tick_loop(), f"majs-tick-{self.route.id}"),
        ]
        # NetRoute:_CheckConnectAvalible：带上线路类型；result 不为 1 说明这条线路拒绝了本次连接
        resp = await self.request(
            ".lq.Route.requestConnection",
            liblq.ReqRequestConnection(
                type=self.route_type,
                route_id=self.route.id,
                timestamp=int(time.time() * 1000),
                platform=DETAIL_PLATFORM,
            ),
            liblq.ResRequestConnection,
        )
        if resp.error.code:
            raise ConnectionError(f"[majs] 路由 {self.route.id} 连接出错: {resp.error}")
        if resp.result != 1:
            raise RouteRejected(f"[majs] 路由 {self.route.id} 拒绝连接: result={resp.result}")
        self.usable = True

    async def close(self) -> None:
        """主动关闭，不触发 on_close。"""
        self._closing = True
        if self._ws is not None:
            await self._ws.close()
        current = asyncio.current_task()
        others = [task for task in self._tasks if task is not current]
        for task in others:
            task.cancel()
        await asyncio.gather(*others, return_exceptions=True)
        self._mark_closed()

    async def request(self, method_name: str, req: betterproto.Message, resp_type: type[RespT]) -> RespT:
        future: asyncio.Future[betterproto.Message] = asyncio.get_running_loop().create_future()
        await self._send(method_name, req, future)
        payload = await future
        if not isinstance(payload, resp_type):
            raise TypeError(f"{method_name} 回包是 {type(payload).__name__}，不是 {resp_type.__name__}")
        return payload

    async def send(self, method_name: str, req: betterproto.Message) -> None:
        """发出去不等回包，和客户端回调里什么都不做的请求一样。"""
        await self._send(method_name, req, None)

    async def send_heartbeat(self, no_operation_counter: int) -> None:
        req = liblq.ReqHeartbeat(
            delay=heartbeat_ms(self.delay()),
            no_operation_counter=no_operation_counter,
            platform=DETAIL_PLATFORM_ID,
            network_quality=heartbeat_ms(self.quality()),
        )
        await self.send(HEARTBEAT, req)

    def need_detect(self) -> bool:
        return time.monotonic() > self.info.detect_end + DETECT_INTERVAL

    def detect(self) -> None:
        """NetRoute:Detect：短时间连发几个 ping 测线路质量。"""
        if self.during_detect or not self.need_detect() or not self.is_open():
            return
        self.during_detect = True
        self.info.last_quality = DELAY_INF
        since = time.monotonic() - self.info.detect_end
        count = 2 + math.floor((since - DETECT_INTERVAL) / 90 * 3) if since < 120 else 5
        self._tasks.append(spawn(self._detect_burst(count), f"majs-detect-{self.route.id}"))

    async def _detect_burst(self, count: int) -> None:
        start = time.monotonic()
        for i in range(count):
            await asyncio.sleep(max(0.0, start + DETECT_FIRST_PING + DETECT_PING_GAP * i - time.monotonic()))
            if not self.is_open():
                return
            await self._ping()
        await asyncio.sleep(max(0.0, start + DETECT_PING_GAP * (count + 1) - time.monotonic()))
        self.info.detect_end = time.monotonic()
        self.during_detect = False
        self.quality()

    async def _ping(self) -> None:
        self._last_ping = time.monotonic()
        try:
            await self.send_heartbeat(0)
        except ConnectionError:
            # 刚好断开：收消息循环会收尾并通知
            return

    async def _send(
        self,
        method_name: str,
        req: betterproto.Message,
        future: asyncio.Future[betterproto.Message] | None,
    ) -> None:
        if self._ws is None or not self.is_open():
            raise ConnectionError(f"[majs] 路由 {self.route.id} 已断开")
        index, data = self._codec.encode_request(method_name, req)
        now = time.monotonic()
        if method_name == HEARTBEAT:
            self._heartbeat_delay.add_waiting(index, now)
        else:
            self._waiting_rpc.add(index)
        self._all_delay.add_waiting(index, now)
        # 先登记再发：回包可能在 send 让出控制权时就到了
        if future is not None:
            self._pending[index] = future
        try:
            await self._ws.send(data)
        except ConnectionClosed as e:
            self._pending.pop(index, None)
            self._waiting_rpc.discard(index)
            self._codec.forget(index)
            raise ConnectionError(f"[majs] 路由 {self.route.id} 已断开") from e

    async def _recv_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                if isinstance(raw, str):
                    continue
                try:
                    msg = self._codec.decode_message(raw)
                except Exception as e:
                    # 服务端数据属于不可信外部输入：解不开就跳过这一条，不断开连接
                    logger.warning(f"[majs] 解析路由 {self.route.id} 的消息失败: {e!r}")
                    continue
                if msg is None:
                    continue
                if msg.msg_type == MajsoulProtoCodec.RESPONSE:
                    self._on_response(msg)
                elif msg.method_name == CONNECTION_SHUTDOWN:
                    # NetRoute:_ParseMessageProto 收到它就关掉路由，之后走重连
                    logger.warning(f"[majs] 服务端关闭路由 {self.route.id}: {msg.payload}")
                    await ws.close()
                    break
                else:
                    self._on_notify(self, msg)
        except ConnectionClosed as e:
            logger.warning(f"[majs] 路由 {self.route.id} 连接断开: {e}")
        finally:
            if self._mark_closed() and not self._closing:
                for task in self._tasks:
                    if task is not asyncio.current_task():
                        task.cancel()
                self._on_close(self)

    def _on_response(self, msg: MajsoulDecodedMessage) -> None:
        now = time.monotonic()
        self._heartbeat_delay.receive(msg.req_index, now)
        self._all_delay.receive(msg.req_index, now)
        self._waiting_rpc.discard(msg.req_index)
        future = self._pending.pop(msg.req_index, None)
        if future is not None and not future.done():
            future.set_result(msg.payload)

    async def _tick_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_INTERVAL)
            now = time.monotonic()
            self._heartbeat_delay.tick(now)
            for index in self._all_delay.tick(now):
                self._codec.forget(index)
                self._waiting_rpc.discard(index)
                future = self._pending.pop(index, None)
                if future is not None and not future.done():
                    future.set_exception(MajsoulRpcTimeout(f"[majs] 请求 {index} 超时"))
            if not self.is_open():
                return
            if not self.during_detect:
                gap = abs(self._all_delay.quality - self._heartbeat_delay.quality)
                interval = ROUTE_PING_INTERVAL_UNSTABLE if gap > ROUTE_QUALITY_GAP else ROUTE_PING_INTERVAL
                if now - self._last_ping >= interval:
                    await self._ping()
            if self._should_expire(now):
                self._expire()
                return

    def _should_expire(self, now: float) -> bool:
        # NetRoute:_Loop：备用、探测线路开太久就关掉，由 NetRouteGroup 再去挑新的
        if self.locked:
            return False
        if self.route_type == ROUTE_TYPE_STANDBY:
            return now - self.opened_at >= STANDBY_MAX_AGE
        if self.route_type == ROUTE_TYPE_DETECT:
            return now - self.opened_at >= DETECT_MAX_AGE
        return False

    def _expire(self) -> None:
        logger.info(f"[majs] 路由 {self.route.id}（类型 {self.route_type}）开满时长，关闭")
        if self._ws is not None and self._expiring is None:
            # 单独起任务关：收消息循环收尾时会取消本循环，别让关闭握手被打断
            self._expiring = spawn(self._ws.close(), f"majs-expire-{self.route.id}")

    def _mark_closed(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(f"[majs] 路由 {self.route.id} 已断开"))
        self._pending.clear()
        self._waiting_rpc.clear()
        return True
