"""一条到路由 /gateway 的 WebSocket，对应客户端的 NetRoute.lua。"""

import math
import time
import asyncio
from typing import TypeVar, Callable, Coroutine

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
    USER_AGENT,
    CONNECT_TIMEOUT,
    DETAIL_PLATFORM,
    ROUTE_TYPE_MAIN,
    ROUTE_QUALITY_GAP,
    DETAIL_PLATFORM_ID,
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


class MajsoulRpcTimeout(TimeoutError):
    pass


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
        route: MajsoulRoute,
        region: MajsoulRegion,
        on_notify: Callable[[MajsoulDecodedMessage], None],
        on_close: Callable[["RouteSocket"], None],
    ) -> None:
        self.route = route
        self._region = region
        self._endpoint = f"{'wss' if route.ssl else 'ws'}://{route.domain}/gateway"
        self._codec = MajsoulProtoCodec(RPC_MAP)
        self._on_notify = on_notify
        self._on_close = on_close
        self._ws: ClientConnection | None = None
        self._pending: dict[int, asyncio.Future[betterproto.Message]] = {}
        # delayWatcher 只记心跳，delayWatcherAll 记全部请求并负责判超时
        self._heartbeat_delay = RouteDelayWatcher()
        self._all_delay = RouteDelayWatcher()
        self._last_ping = 0.0
        self._tasks: list[asyncio.Task[None]] = []
        self._closing = False
        self._closed = False

    def is_open(self) -> bool:
        return self._ws is not None and not self._closed and self._ws.state is State.OPEN

    async def open(self) -> None:
        logger.info(f"[majs] 连接路由 {self.route.id}: {self._endpoint}")
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
        self._tasks = [
            spawn(self._recv_loop(), f"majs-recv-{self.route.id}"),
            spawn(self._tick_loop(), f"majs-tick-{self.route.id}"),
        ]
        # NetRoute:_CheckConnectAvalible：result 不为 1 说明这条路由拒绝了本次连接
        resp = await self.request(
            ".lq.Route.requestConnection",
            liblq.ReqRequestConnection(
                type=ROUTE_TYPE_MAIN,
                route_id=self.route.id,
                timestamp=int(time.time() * 1000),
                platform=DETAIL_PLATFORM,
            ),
            liblq.ResRequestConnection,
        )
        if resp.error.code or resp.result != 1:
            raise ConnectionError(f"[majs] 路由 {self.route.id} 拒绝连接: {resp}")

    async def close(self) -> None:
        """主动关闭，不触发重连。"""
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
            delay=heartbeat_ms(self._heartbeat_delay.delay),
            no_operation_counter=no_operation_counter,
            platform=DETAIL_PLATFORM_ID,
            network_quality=heartbeat_ms(self._heartbeat_delay.quality),
        )
        await self.send(HEARTBEAT, req)

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
        self._all_delay.add_waiting(index, now)
        # 先登记再发：回包可能在 send 让出控制权时就到了
        if future is not None:
            self._pending[index] = future
        try:
            await self._ws.send(data)
        except ConnectionClosed as e:
            self._pending.pop(index, None)
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
                    self._on_notify(msg)
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
                future = self._pending.pop(index, None)
                if future is not None and not future.done():
                    future.set_exception(MajsoulRpcTimeout(f"[majs] 请求 {index} 超时"))
            gap = abs(self._all_delay.quality - self._heartbeat_delay.quality)
            interval = ROUTE_PING_INTERVAL_UNSTABLE if gap > ROUTE_QUALITY_GAP else ROUTE_PING_INTERVAL
            if now - self._last_ping >= interval:
                self._last_ping = now
                if not self.is_open():
                    return
                try:
                    await self.send_heartbeat(0)
                except ConnectionError:
                    # 刚好断开：收消息循环会收尾并通知重连
                    return

    def _mark_closed(self) -> bool:
        if self._closed:
            return False
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(f"[majs] 路由 {self.route.id} 已断开"))
        self._pending.clear()
        return True
