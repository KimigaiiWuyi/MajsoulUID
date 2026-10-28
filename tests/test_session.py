"""账号会话：登录、重连、被顶号、心跳，行为对照官方 Unity 客户端的 Lua。"""

import sys
import hmac
import types
import random
import struct
import asyncio
import hashlib
import importlib
from pathlib import Path

import pytest
from websockets.asyncio.server import ServerConnection, serve

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "MajsoulUID"
PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"

# majs_notify/__init__.py 会拉起数据库与定时任务，这里只占位包、单独加载 majsoul
if f"{PKG}.majs_notify" not in sys.modules:
    stub = types.ModuleType(f"{PKG}.majs_notify")
    stub.__path__ = [str(PLUGIN_ROOT / "majs_notify")]
    sys.modules[f"{PKG}.majs_notify"] = stub

majsoul = importlib.import_module(f"{PKG}.majs_notify.majsoul")
route_mod = importlib.import_module(f"{PKG}.majs_notify.route")
route_delay = importlib.import_module(f"{PKG}.majs_notify.route_delay")
constants = importlib.import_module(f"{PKG}.majs_notify.constants")
model = importlib.import_module(f"{PKG}.majs_notify.model")
account_input = importlib.import_module(f"{PKG}.majs_notify.account_input")
liblq = importlib.import_module(f"{PKG}.lib.lq")

ConnState = majsoul.ConnState
ROUTE = model.MajsoulRoute(id="r1", domain="example.invalid", ssl=True)


def ok_login(nickname: str = "小号", access_token: str = ""):
    return liblq.ResLogin(account_id=42, account=liblq.Account(nickname=nickname), access_token=access_token)


def notice(payload):
    return model.MajsoulDecodedMessage(
        msg_type=1, req_index=0, method_name=f".lq.{type(payload).__name__}", payload=payload
    )


class FakeServer:
    """按方法名排好回包；回包可以是函数，拿到 (socket, 请求) 再决定返回什么。"""

    def __init__(self) -> None:
        self.script: dict[str, list] = {}
        self.calls: list[str] = []
        self.requests: dict[str, list] = {}
        self.sent: list[str] = []
        self.sockets: list["FakeSocket"] = []
        self.fail_opens = 0
        self.opens = 0

    def reply(self, method: str, *responses) -> None:
        self.script.setdefault(f".lq.Lobby.{method}", []).extend(responses)


class FakeSocket:
    server: FakeServer

    def __init__(self, route, region, on_notify, on_close) -> None:
        self.route = route
        self.on_notify = on_notify
        self.on_close = on_close
        self.closed = False
        self.server.sockets.append(self)

    async def open(self) -> None:
        self.server.opens += 1
        if self.server.fail_opens:
            self.server.fail_opens -= 1
            raise ConnectionError("refused")

    async def close(self) -> None:
        self.closed = True

    def is_open(self) -> bool:
        return not self.closed

    async def request(self, method_name, req, resp_type):
        self.server.calls.append(method_name.rsplit(".", 1)[-1])
        self.server.requests.setdefault(method_name, []).append(req)
        resp = self.server.script[method_name].pop(0)
        if callable(resp):
            resp = resp(self, req)
        assert isinstance(resp, resp_type)
        return resp

    async def send(self, method_name, req) -> None:
        self.server.sent.append(method_name.rsplit(".", 1)[-1])

    async def send_heartbeat(self, no_operation_counter: int) -> None:
        self.server.sent.append(f"heartbeat:{no_operation_counter}")


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    srv = FakeServer()
    FakeSocket.server = srv

    async def fetch_route(region):
        return ROUTE

    async def update_data_by_data(select: dict, update: dict) -> int:
        return 0

    monkeypatch.setattr(majsoul, "RouteSocket", FakeSocket)
    monkeypatch.setattr(majsoul, "fetch_route", fetch_route)
    monkeypatch.setattr(majsoul, "MajsUser", types.SimpleNamespace(update_data_by_data=update_data_by_data))
    monkeypatch.setattr(majsoul, "RECONNECT_DELAYS", (0, 0, 0, 0, 0, 0))
    monkeypatch.setattr(majsoul, "LOGIN_BEAT_RETRY_DELAY", 0)
    monkeypatch.setattr(majsoul, "LOGIN_QUEUE_POLL_INTERVAL", 0.01)
    return srv


def make_conn(**credential: str):
    conn = majsoul.MajsoulConnection(majsoul.MajsoulCredential(login_type=0, **credential), "device")
    conn.metas = []

    async def send_meta(msg: str) -> None:
        conn.metas.append(msg)

    conn.send_meta = send_meta
    return conn


def script_token_login(server: FakeServer, nickname: str = "小号") -> None:
    server.reply("oauth2Check", liblq.ResOauth2Check(has_account=True))
    server.reply("oauth2Login", ok_login(nickname))
    server.reply("loginBeat", liblq.ResCommon())
    if nickname:
        server.reply("loginSuccess", liblq.ResCommon())
    server.reply("fetchInfo", liblq.ResFetchInfo())


async def finish(conn) -> None:
    await asyncio.gather(*conn._notify_tasks)
    if conn._reconnect_task is not None:
        await conn._reconnect_task
    await conn.close()


def test_token_login_follows_client_order(server: FakeServer):
    script_token_login(server)

    async def scenario():
        conn = make_conn(username="a@b.com", access_token="tok")
        await conn.login()
        state = conn.state
        await finish(conn)
        return state

    assert asyncio.run(scenario()) is ConnState.ONLINE
    # CheckToken → LoginByToken → OnLoginSuccess 里发 loginBeat → 有昵称再报 loginSuccess
    assert server.calls == ["oauth2Check", "oauth2Login", "loginBeat", "loginSuccess", "fetchInfo"]
    req = server.requests[".lq.Lobby.oauth2Login"][0]
    assert (req.type, req.access_token, req.reconnect) == (0, "tok", False)


@pytest.mark.parametrize(
    ("username", "login_type"),
    [("someone@example.com", constants.LOGIN_TYPE_ACCOUNT), ("13800138000", constants.LOGIN_TYPE_PHONE)],
)
def test_expired_token_falls_back_to_password(server: FakeServer, username: str, login_type: int):
    server.reply("oauth2Check", liblq.ResOauth2Check(has_account=False))
    server.reply("login", ok_login(nickname="", access_token="new-token"))
    server.reply("loginBeat", liblq.ResCommon())
    server.reply("fetchInfo", liblq.ResFetchInfo())

    async def scenario():
        conn = make_conn(username=username, password="pw", access_token="old")
        await conn.login()
        token = conn.access_token
        await finish(conn)
        return token

    assert asyncio.run(scenario()) == "new-token"
    # 没昵称的号不报 loginSuccess（UI_Entrance:OnLoginSuccess）
    assert server.calls == ["oauth2Check", "login", "loginBeat", "fetchInfo"]
    req = server.requests[".lq.Lobby.login"][0]
    assert req.password == hmac.new(b"lailai", b"pw", hashlib.sha256).hexdigest()
    assert (req.account, req.type, req.gen_access_token, req.reconnect) == (username, login_type, True, False)


def test_banned_account_fails_without_fallback(server: FakeServer):
    server.reply("login", liblq.ResLogin(error=liblq.Error(code=constants.ERR_PROHIBITED)))

    async def scenario():
        conn = make_conn(username="u", password="pw")
        with pytest.raises(majsoul.MajsoulBannedError):
            await conn.login()
        return conn

    conn = asyncio.run(scenario())
    assert conn.state is ConnState.FAILED
    assert "封禁" in conn.last_error
    assert server.sockets[0].closed


def test_login_queue_waits_for_finish_notice(server: FakeServer):
    def queue_info(socket: FakeSocket, req):
        # 排了一会儿，服务端推送 NotifyLoginQueueFinished
        asyncio.get_running_loop().call_later(0.03, socket.on_notify, notice(liblq.NotifyLoginQueueFinished()))
        return liblq.ResFetchQueueInfo(rank=3, remain=30)

    server.reply("login", liblq.ResLogin(error=liblq.Error(code=constants.ERR_LOGIN_QUEUE)), ok_login())
    server.reply("fetchQueueInfo", queue_info, *[liblq.ResFetchQueueInfo(rank=1) for _ in range(20)])
    server.reply("loginBeat", liblq.ResCommon())
    server.reply("loginSuccess", liblq.ResCommon())
    server.reply("fetchInfo", liblq.ResFetchInfo())

    async def scenario():
        conn = make_conn(username="u", password="pw")
        await conn.login()
        state = conn.state
        await finish(conn)
        return state

    assert asyncio.run(scenario()) is ConnState.ONLINE
    calls = [call for call in server.calls if call != "fetchQueueInfo"]
    assert calls == ["login", "login", "loginBeat", "loginSuccess", "fetchInfo"]
    assert server.calls[1] == "fetchQueueInfo"


def test_login_beat_retries_once(server: FakeServer):
    script_token_login(server)
    server.script[".lq.Lobby.loginBeat"].insert(0, liblq.ResCommon(error=liblq.Error(code=1)))

    async def scenario():
        conn = make_conn(username="u", access_token="tok")
        await conn.login()
        await finish(conn)

    asyncio.run(scenario())
    assert server.calls.count("loginBeat") == 2


def test_reconnect_relogs_in_with_reconnect_flag(server: FakeServer):
    script_token_login(server)
    server.reply("oauth2Login", ok_login())
    server.reply("loginBeat", liblq.ResCommon())
    server.reply("loginSuccess", liblq.ResCommon())
    server.reply("fetchInfo", liblq.ResFetchInfo())

    async def scenario():
        conn = make_conn(username="u", access_token="tok")
        await conn.login()
        server.calls.clear()
        server.fail_opens = 2
        first = server.sockets[0]
        first.on_close(first)
        assert conn.state is ConnState.RECONNECTING
        await conn._reconnect_task
        state = conn.state
        await finish(conn)
        return state

    assert asyncio.run(scenario()) is ConnState.ONLINE
    # NetRouteGroup 在同一条路由上重连；LoginMgr._OnReloginSuccess 的顺序
    assert server.opens == 4
    assert [s.route for s in server.sockets] == [ROUTE] * 4
    assert server.calls == ["oauth2Login", "loginBeat", "loginSuccess", "fetchInfo"]
    assert "fetchServerTime" in server.sent
    req = server.requests[".lq.Lobby.oauth2Login"][-1]
    assert (req.access_token, req.reconnect) == ("tok", True)


def test_reconnect_gives_up_after_client_attempts(server: FakeServer):
    script_token_login(server)

    async def scenario():
        conn = make_conn(username="u", access_token="tok")
        await conn.login()
        server.fail_opens = 99
        server.sockets[0].on_close(server.sockets[0])
        await conn._reconnect_task
        return conn

    conn = asyncio.run(scenario())
    assert conn.state is ConnState.FAILED
    assert server.opens == 1 + len(constants.RECONNECT_DELAYS)
    assert "重连 6 次" in conn.metas[0]


def test_rejected_relogin_is_not_retried(server: FakeServer):
    script_token_login(server)
    server.reply("oauth2Login", liblq.ResLogin(error=liblq.Error(code=1003)))

    async def scenario():
        conn = make_conn(username="u", access_token="tok")
        await conn.login()
        server.sockets[0].on_close(server.sockets[0])
        await conn._reconnect_task
        return conn

    conn = asyncio.run(scenario())
    assert conn.state is ConnState.FAILED
    assert server.opens == 2
    assert "自动重连登录失败" in conn.metas[0]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [(liblq.NotifyAnotherLogin(), "在别处登录"), (liblq.NotifyAccountLogout(), "被服务端登出")],
)
def test_kicked_relogs_in_once_then_stops(server: FakeServer, payload, reason: str):
    for _ in range(2):
        script_token_login(server)

    async def scenario():
        conn = make_conn(username="u", access_token="tok")
        await conn.login()
        first = server.sockets[0]
        first.on_notify(notice(payload))
        # 服务端紧接着断开也不能当成掉线去重连
        first.on_close(first)
        assert conn._reconnect_task is None
        await asyncio.gather(*conn._notify_tasks)
        after_first = conn.state
        server.sockets[1].on_notify(notice(payload))
        await asyncio.gather(*conn._notify_tasks)
        return conn, after_first

    conn, after_first = asyncio.run(scenario())
    assert after_first is ConnState.ONLINE
    assert conn.state is ConnState.KICKED
    assert server.calls.count("oauth2Login") == 2
    assert conn.metas == [
        f"账号 小号(42) {reason}，自动重新登录一次",
        f"账号 小号(42) 又{reason}，不再自动重新登录。\n"
        "反复出现说明还有别的程序在登录这个账号，处理后发送[雀魂重启服务]",
    ]


def test_duplicate_kick_notice_counts_once(server: FakeServer):
    # 同一次被顶号如果收到两条通知，不能把「重登一次」的机会用掉还误报「不再自动重登」
    script_token_login(server)
    script_token_login(server)

    async def scenario():
        conn = make_conn(username="u", access_token="tok")
        await conn.login()
        first = server.sockets[0]
        first.on_notify(notice(liblq.NotifyAnotherLogin()))
        first.on_notify(notice(liblq.NotifyAccountLogout()))
        await asyncio.gather(*conn._notify_tasks)
        state = conn.state
        await finish(conn)
        return conn, state

    conn, state = asyncio.run(scenario())
    assert state is ConnState.ONLINE
    assert server.calls.count("oauth2Login") == 2
    assert conn.metas == ["账号 小号(42) 在别处登录，自动重新登录一次"]


def boom(socket, req):
    raise RuntimeError("database is locked")


def test_unexpected_error_during_reconnect_does_not_stick(server: FakeServer):
    script_token_login(server)
    server.reply("oauth2Login", boom)

    async def scenario():
        conn = make_conn(username="u", access_token="tok")
        await conn.login()
        server.sockets[0].on_close(server.sockets[0])
        with pytest.raises(RuntimeError):
            await conn._reconnect_task
        return conn

    conn = asyncio.run(scenario())
    # 之前会一直卡在「重连中」：get_conn 找不到号，start 又跳过重连中的号
    assert conn.state is ConnState.FAILED
    assert "重连时出错" in conn.metas[0]
    assert server.sockets[-1].closed


def test_unexpected_error_during_login_closes_socket(server: FakeServer):
    server.reply("oauth2Check", boom)
    conn = make_conn(username="u", access_token="tok")
    with pytest.raises(RuntimeError):
        asyncio.run(conn.login())
    assert conn.state is ConnState.FAILED
    assert server.sockets[0].closed


def test_token_save_failure_does_not_block_login(server: FakeServer, monkeypatch: pytest.MonkeyPatch):
    async def broken_update(select: dict, update: dict) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(majsoul, "MajsUser", types.SimpleNamespace(update_data_by_data=broken_update))
    server.reply("oauth2Check", liblq.ResOauth2Check(has_account=False))
    server.reply("login", ok_login(access_token="new-token"))
    server.reply("loginBeat", liblq.ResCommon())
    server.reply("loginSuccess", liblq.ResCommon())
    server.reply("fetchInfo", liblq.ResFetchInfo())

    async def scenario():
        conn = make_conn(username="u", password="pw", access_token="old")
        await conn.login()
        saves = await asyncio.gather(*conn._side_tasks, return_exceptions=True)
        state = conn.state
        await finish(conn)
        return state, saves

    state, saves = asyncio.run(scenario())
    assert state is ConnState.ONLINE
    assert [type(e) for e in saves] == [RuntimeError]


def test_manual_start_gives_kicked_account_another_relogin(server: FakeServer):
    conn = make_conn(username="u", access_token="tok")
    conn._kick_relogin_used = True
    conn.reset_kick()
    assert conn._kick_relogin_used is False


def test_idle_counter_never_reaches_hangup_logout(monkeypatch: pytest.MonkeyPatch):
    # GameMgr：挂机满 50 分钟客户端会自己登出，真客户端不会报出这么大的挂机秒数
    random.seed(7)
    for chance in (0.0, 0.6):
        monkeypatch.setattr(majsoul.IdleSimulator, "MOVE_CHANCE", chance)
        idle = majsoul.IdleSimulator()
        now = idle._last_move
        samples = []
        for _ in range(500):
            now += constants.LOBBY_PING_INTERVAL
            samples.append(idle.sample(now))
        assert max(samples) < constants.HANGUP_LOGOUT_SECONDS
        assert min(samples) >= 0
    # 一直不动鼠标时挂机秒数会涨上去，而不是一直报 0
    assert max(samples) > constants.LOBBY_PING_INTERVAL


def test_heartbeat_fields_clamp_like_client():
    assert route_mod.heartbeat_ms(constants.DELAY_INF) == constants.HEARTBEAT_FIELD_MAX
    assert route_mod.heartbeat_ms(0.0456) == 45
    assert route_mod.heartbeat_ms(-1) == 0


def test_delay_watcher_times_out_after_ten_seconds():
    watcher = route_delay.RouteDelayWatcher()
    watcher.add_waiting(1, 0.0)
    watcher.add_waiting(2, 1.0)
    assert watcher.tick(5.0) == []
    assert watcher.delay == pytest.approx(5.0)
    watcher.receive(2, 5.5)
    assert watcher.tick(10.5) == [1]
    watcher.receive(1, 11.0)
    assert watcher.tick(20.0) == []


def test_quality_uses_second_largest_latency():
    analyzer = route_delay.NetworkQualityAnalyzer()
    for stamp, latency in enumerate([0.05, 0.4, 0.06, 0.9, 0.05]):
        analyzer.add_latency_sample(float(stamp), latency)
    assert analyzer.stability_score() == pytest.approx(0.4)
    analyzer.add_latency_sample(100.0, 0.05)
    assert analyzer.stability_score() == pytest.approx(0.05)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("用户名, 密码", ("用户名", "密码")),
        ("user pass", ("user", "pass")),
        ("user，pass", ("user", "pass")),
        ("user,pass", ("user", "pass")),
        ("a b c", None),
        ("", None),
    ],
)
def test_parse_account_password(text: str, expected):
    # 帮助里的示例是「用户名, 密码」，之前按单个空格切，照示例输入会抛 ValueError
    assert account_input.parse_account_password(text) == expected


def response_bytes(index: int, payload) -> bytes:
    body = bytes(liblq.Wrapper(name="", data=bytes(payload)))
    return struct.pack("<BH", 3, index) + body


def notify_bytes(name: str, payload: bytes) -> bytes:
    return bytes([1]) + bytes(liblq.Wrapper(name=name, data=payload))


async def run_with_server(handler, body, **kwargs):
    async with serve(handler, "127.0.0.1", 0, **kwargs) as srv:
        port = list(srv.sockets)[0].getsockname()[1]
        route = model.MajsoulRoute(id="local", domain=f"127.0.0.1:{port}", ssl=False)
        return await body(route)


def test_route_socket_skips_unknown_notify_and_reports_shutdown():
    received: list[str] = []
    headers: list[str | None] = []

    async def handler(ws: ServerConnection):
        headers.append(ws.request.headers["Origin"] if ws.request else None)
        async for raw in ws:
            assert isinstance(raw, bytes)
            index = raw[1] | (raw[2] << 8)
            name = liblq.Wrapper().parse(raw[3:]).name
            received.append(name)
            if name == ".lq.Route.requestConnection":
                await ws.send(response_bytes(index, liblq.ResRequestConnection(result=1)))
            elif name == ".lq.Lobby.loginSuccess":
                await ws.send(notify_bytes(".lq.NotifyFromTheFuture", b"\x08\x01"))
                await ws.send(notify_bytes(".lq.NotifyNewFriendApply", bytes(liblq.NotifyNewFriendApply(account_id=9))))
                await ws.send(response_bytes(index, liblq.ResCommon()))
            elif name == ".lq.Lobby.fetchInfo":
                shutdown = liblq.NotifyConnectionShutdown(reason=1)
                await ws.send(notify_bytes(".lq.NotifyConnectionShutdown", bytes(shutdown)))

    async def body(route):
        notices: list[str] = []
        closed = asyncio.Event()
        socket = route_mod.RouteSocket(
            route, constants.REGION_CN, lambda msg: notices.append(msg.method_name), lambda s: closed.set()
        )
        await socket.open()
        resp = await socket.request(".lq.Lobby.loginSuccess", liblq.ReqCommon(), liblq.ResCommon)
        # 等过第一轮 tick，路由心跳就该发出去了
        await asyncio.sleep(route_mod.TICK_INTERVAL + 0.2)
        await socket.send(".lq.Lobby.fetchInfo", liblq.ReqCommon())
        await asyncio.wait_for(closed.wait(), 5)
        return resp, notices, socket.is_open()

    resp, notices, still_open = asyncio.run(run_with_server(handler, body))
    assert isinstance(resp, liblq.ResCommon)
    assert notices == [".lq.NotifyNewFriendApply"]
    assert not still_open
    assert headers == ["https://game.maj-soul.com"]
    # 连上就先发 requestConnection；路由心跳在第一轮 tick 里发出
    assert received[0] == ".lq.Route.requestConnection"
    assert ".lq.Route.heartbeat" in received


def test_route_socket_request_times_out(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(route_delay, "RPC_TIMEOUT", 0.3)

    async def handler(ws: ServerConnection):
        async for raw in ws:
            assert isinstance(raw, bytes)
            index = raw[1] | (raw[2] << 8)
            if liblq.Wrapper().parse(raw[3:]).name == ".lq.Route.requestConnection":
                await ws.send(response_bytes(index, liblq.ResRequestConnection(result=1)))

    async def body(route):
        closed: list[bool] = []
        socket = route_mod.RouteSocket(route, constants.REGION_CN, lambda msg: None, lambda s: closed.append(True))
        await socket.open()
        with pytest.raises(route_mod.MajsoulRpcTimeout):
            await socket.request(".lq.Lobby.fetchInfo", liblq.ReqCommon(), liblq.ResFetchInfo)
        # 超时不断开连接，主动关闭也不会触发重连回调
        still_open = socket.is_open()
        await socket.close()
        return still_open, closed

    still_open, closed = asyncio.run(run_with_server(handler, body))
    assert still_open
    assert closed == []


def test_route_socket_closed_during_handshake_leaves_nothing_running():
    async def slow_handshake(connection: ServerConnection, request):
        await asyncio.sleep(0.3)

    async def handler(ws: ServerConnection):
        await ws.wait_closed()

    async def body(route):
        socket = route_mod.RouteSocket(route, constants.REGION_CN, lambda msg: None, lambda s: None)
        opening = asyncio.create_task(socket.open())
        await asyncio.sleep(0.1)
        await socket.close()
        with pytest.raises(ConnectionError):
            await opening
        return socket.is_open(), socket._tasks

    still_open, tasks = asyncio.run(run_with_server(handler, body, process_request=slow_handshake))
    assert not still_open
    assert tasks == []
