"""主 / 备用 / 探测线路的管理，对照客户端 NetRouteGroup.lua、GatewayFetcher.lua。"""

import time
import random
import struct
import asyncio
import importlib

import pytest
from route_fakes import PKG, FakeServer, FakeSocket, FakeGateway, FakeListener, make_route
from websockets.asyncio.server import ServerConnection, serve

route_mod = importlib.import_module(f"{PKG}.majs_notify.route")
route_group = importlib.import_module(f"{PKG}.majs_notify.route_group")
constants = importlib.import_module(f"{PKG}.majs_notify.constants")
model = importlib.import_module(f"{PKG}.majs_notify.model")
liblq = importlib.import_module(f"{PKG}.lib.lq")

MAIN = constants.ROUTE_TYPE_MAIN
STANDBY = constants.ROUTE_TYPE_STANDBY
DETECT = constants.ROUTE_TYPE_DETECT


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    srv = FakeServer()
    FakeSocket.server = srv
    monkeypatch.setattr(route_group, "RouteSocket", FakeSocket)
    monkeypatch.setattr(route_group, "RECONNECT_DELAYS", (0, 0, 0, 0, 0, 0))
    return srv


def make_group(routes=None):
    gateway = FakeGateway(routes or [make_route("r1", level=1), make_route("r2"), make_route("r3")])
    listener = FakeListener()
    group = route_group.RouteGroup(constants.REGION_CN, gateway, listener)
    group.sync_routes()
    group.closed = False
    return group, listener, gateway


def open_socket(group, rid: str, route_type: int, age: float = 100.0) -> FakeSocket:
    socket = group._new_socket(group.routes[rid], route_type)
    socket.usable = True
    socket.opened_at = time.monotonic() - age
    # 刚测过速，不用再测
    socket.info.detect_end = time.monotonic()
    return socket


async def run_jobs(group) -> None:
    while group._jobs:
        await asyncio.gather(*list(group._jobs), return_exceptions=True)


def test_entrance_prefers_last_route_then_highest_level(server: FakeServer):
    routes = [make_route("r1"), make_route("r2", level=2), make_route("r3", level=2, state="busy")]

    async def scenario(preferred: str) -> str:
        group, _, _ = make_group(routes)
        socket = await group.open_entrance(preferred)
        return socket.route.id

    # 没有上次的线路：只在能用的里挑 level 最高的（r3 忙，不算）
    assert asyncio.run(scenario("")) == "r2"
    assert asyncio.run(scenario("r1")) == "r1"
    assert asyncio.run(scenario("r3")) == "r2"


def test_only_main_route_notices_are_handled(server: FakeServer):
    group, listener, _ = make_group()
    group.main = open_socket(group, "r1", MAIN)
    group.standby = open_socket(group, "r2", STANDBY)
    msg = model.MajsoulDecodedMessage(msg_type=1, req_index=0, method_name=".lq.NotifyFriendChange", payload=None)
    group._on_notify(group.standby, msg)
    group._on_notify(group.main, msg)
    assert listener.notices == [".lq.NotifyFriendChange"]


def test_detect_opens_standby_and_prepares_login(server: FakeServer):
    server.reply("prepareLogin", liblq.ResCommon())

    async def scenario():
        group, _, _ = make_group()
        group.main = open_socket(group, "r1", MAIN)
        group._tick_detect(time.monotonic())
        standby = group.standby
        assert standby is not None and standby.locked and standby.route_type == STANDBY
        await run_jobs(group)
        return group, standby

    group, standby = asyncio.run(scenario())
    # _OnRouteUseable：备用线路预登录 + 测速，完事解锁
    assert group.standby is standby and not standby.locked
    assert standby.route.id in ("r2", "r3") and standby.detects == 1
    req = server.requests[".lq.Lobby.prepareLogin"][0]
    assert (req.access_token, req.type) == ("tok", 0)


def test_failed_prepare_login_bans_standby(server: FakeServer):
    server.reply("prepareLogin", liblq.ResCommon(error=liblq.Error(code=1)))

    async def scenario():
        group, _, _ = make_group()
        group.main = open_socket(group, "r1", MAIN)
        group._tick_detect(time.monotonic())
        standby = group.standby
        await run_jobs(group)
        return group, standby

    group, standby = asyncio.run(scenario())
    assert group.standby is None and standby.closed
    assert standby.info.banned_until > time.monotonic() + constants.BAN_PREPARE_FAILED - 5


def test_black_hole_switches_to_standby_with_fast_login(server: FakeServer):
    server.quality.update(r1=5.0, r2=0.1)
    server.reply("requestRouteChange", liblq.ResRequestRouteChange(result=1))
    server.reply("fastLogin", liblq.ResFastLogin())

    async def scenario():
        group, listener, _ = make_group()
        old = group.main = open_socket(group, "r1", MAIN)
        group.standby = open_socket(group, "r2", STANDBY)
        now = time.monotonic()
        group._tick_black_hole(now)
        assert not group._jobs
        # 质量超过 4 秒持续 5 秒以上才算黑洞
        group._tick_black_hole(now + constants.BLACK_HOLE_SECONDS + 1)
        await run_jobs(group)
        return group, listener, old

    group, listener, old = asyncio.run(scenario())
    assert group.main.route.id == "r2" and group.main.route_type == MAIN and group.standby is None
    assert old.closed and old.info.banned_until > time.monotonic() + 50
    req = server.requests[".lq.Route.requestRouteChange"][0]
    assert (req.before, req.route_id, req.type) == ("r1", "r2", MAIN)
    assert server.calls == ["requestRouteChange", "fastLogin"]
    assert listener.reconnected == [True]


def test_black_hole_waits_while_main_just_opened(server: FakeServer):
    server.quality.update(r1=5.0, r2=0.1)
    group, _, _ = make_group()
    group.main = open_socket(group, "r1", MAIN, age=2)
    group.standby = open_socket(group, "r2", STANDBY)
    now = time.monotonic()
    group._tick_black_hole(now)
    group._tick_black_hole(now + 6)
    assert not group._jobs and group._black_hole_since is None


def test_reconnecting_over_twenty_seconds_switches_to_standby(server: FakeServer):
    server.reply("requestRouteChange", liblq.ResRequestRouteChange(result=1))
    server.reply("fastLogin", liblq.ResFastLogin())

    async def scenario():
        group, listener, _ = make_group()
        dead = group.main = group._new_socket(group.routes["r1"], MAIN)
        group.standby = open_socket(group, "r2", STANDBY)
        group.reconnecting = True
        now = time.monotonic()
        group._reconnect_started = now - constants.RECONNECT_SWITCH_AFTER + 1
        group._tick_black_hole(now)
        assert not group._jobs
        group._reconnect_started = now - constants.RECONNECT_SWITCH_AFTER - 1
        group._tick_black_hole(now)
        await run_jobs(group)
        return group, listener, dead

    group, listener, dead = asyncio.run(scenario())
    assert group.main.route.id == "r2" and not group.reconnecting
    assert dead.closed
    assert listener.reconnected == [True]


def test_failed_switch_while_reconnecting_gives_up(server: FakeServer):
    server.reply("requestRouteChange", liblq.ResRequestRouteChange(result=0))

    async def scenario():
        group, listener, _ = make_group()
        group.main = group._new_socket(group.routes["r1"], MAIN)
        standby = group.standby = open_socket(group, "r2", STANDBY)
        group.reconnecting = True
        group._reconnect_started = time.monotonic() - 30
        group._tick_black_hole(time.monotonic())
        await run_jobs(group)
        return group, listener, standby

    group, listener, standby = asyncio.run(scenario())
    assert standby.closed and standby.info.banned_until > time.monotonic() + 250
    assert listener.failed == ["断线后切换备用线路失败"] and group.closed


def test_detect_route_replaces_worse_standby(server: FakeServer):
    server.quality.update(r2=0.8, r3=0.2)
    server.reply("requestRouteChange", liblq.ResRequestRouteChange(result=1))
    server.reply("prepareLogin", liblq.ResCommon())

    async def scenario():
        group, _, _ = make_group()
        group.main = open_socket(group, "r1", MAIN)
        old = group.standby = open_socket(group, "r2", STANDBY)
        group.detect = open_socket(group, "r3", DETECT)
        group._tick_detect(time.monotonic())
        await run_jobs(group)
        return group, old

    group, old = asyncio.run(scenario())
    assert group.standby.route.id == "r3" and group.standby.route_type == STANDBY and group.detect is None
    assert old.closed
    req = server.requests[".lq.Route.requestRouteChange"][0]
    assert (req.before, req.route_id, req.type) == ("r2", "r3", STANDBY)
    assert server.calls == ["requestRouteChange", "prepareLogin"]


@pytest.mark.parametrize(
    ("detect_quality", "ban"), [(0.75, constants.BAN_DETECT_WORSE), (1.5, constants.BAN_DETECT_BAD)]
)
def test_worse_detect_route_is_closed_and_banned(server: FakeServer, detect_quality: float, ban: float):
    server.quality.update(r2=0.8, r3=detect_quality)

    async def scenario():
        group, _, _ = make_group()
        group.main = open_socket(group, "r1", MAIN)
        group.standby = open_socket(group, "r2", STANDBY)
        detect = group.detect = open_socket(group, "r3", DETECT)
        group._tick_detect(time.monotonic())
        await run_jobs(group)
        return group, detect

    group, detect = asyncio.run(scenario())
    assert group.detect is None and detect.closed
    assert detect.info.banned_until == pytest.approx(time.monotonic() + ban, abs=5)


def test_good_standby_needs_no_detect(server: FakeServer):
    server.quality.update(r2=0.1)
    group, _, gateway = make_group()
    group.main = open_socket(group, "r1", MAIN)
    group.standby = open_socket(group, "r2", STANDBY)
    group._tick_detect(time.monotonic())
    assert group.detect is None and gateway.checks == 0


def test_detect_cooldown_matches_client():
    group, _, _ = make_group()
    assert group._detect_cooldown(constants.DELAY_INF) == constants.DETECT_COOLDOWN_MIN
    assert group._detect_cooldown(0.2) == pytest.approx(constants.DETECT_COOLDOWN_MAX)
    assert group._detect_cooldown(0.6) == pytest.approx((180 + 3) / 2)


def test_detect_only_picks_idle_unbanned_routes(server: FakeServer):
    routes = [make_route("r1"), make_route("r2", state="normal"), make_route("r3"), make_route("r4")]
    group, _, _ = make_group(routes)
    group.main = open_socket(group, "r1", MAIN)
    group.routes["r3"].ban(60)
    server.reply("prepareLogin", liblq.ResCommon())

    async def scenario() -> str:
        random.seed(1)
        group._start_detect(time.monotonic(), constants.DELAY_INF)
        picked = group.standby.route.id if group.standby is not None else ""
        await run_jobs(group)
        return picked

    # r1 是主线路，r2 不是 idle，r3 还在封禁
    assert asyncio.run(scenario()) == "r4"


def test_gateway_refresh_closes_unusable_standby(server: FakeServer):
    group, _, gateway = make_group()
    group.main = open_socket(group, "r1", MAIN)
    standby = group.standby = open_socket(group, "r2", STANDBY)
    gateway.routes["r2"] = make_route("r2", state="busy")
    gateway.routes["r4"] = make_route("r4")

    async def scenario():
        group.sync_routes()
        await run_jobs(group)

    asyncio.run(scenario())
    assert group.standby is None and standby.closed
    assert "r4" in group.routes and group.routes["r2"].busy_state == "busy"


def test_gateway_marks_missing_routes_removed():
    gateway = route_group.GatewayFetcher(constants.REGION_CN)
    data = model.MajsoulRoutesData(routes=[make_route("r1"), make_route("r2")])
    gateway._apply("gw-a", data)
    gateway._apply("gw-b", model.MajsoulRoutesData(routes=[make_route("r2")]))
    assert gateway.routes["r1"].state == constants.BUSY_REMOVED
    assert gateway.routes["r2"].state == "idle"
    assert gateway._preferred == "gw-b"
    # 刚拉过：两分钟内不用再刷新
    assert gateway.check_fetch() is False


def test_gateway_asks_last_good_gateway_first(monkeypatch: pytest.MonkeyPatch):
    region = constants.REGION_CN
    gateway = route_group.GatewayFetcher(region)
    gateway._preferred = region.gateways[2]
    asked: list[str] = []

    async def try_gateway(url: str):
        asked.append(url)
        return url, model.MajsoulRoutesData(routes=[make_route("r1")])

    monkeypatch.setattr(gateway, "_try_gateway", try_gateway)
    assert asyncio.run(gateway.fetch()) is True
    assert asked == [region.gateways[2]]


def test_gateway_all_failed_clears_routes(monkeypatch: pytest.MonkeyPatch):
    gateway = route_group.GatewayFetcher(constants.REGION_JP)
    gateway._apply("gw", model.MajsoulRoutesData(routes=[make_route("r1")]))
    monkeypatch.setattr(route_group, "GATEWAY_RETRY_DELAY", 0.01)

    async def try_gateway(url: str):
        return None

    monkeypatch.setattr(gateway, "_try_gateway", try_gateway)
    assert asyncio.run(gateway.fetch()) is False
    assert gateway.routes == {} and gateway._fails == 1


def test_real_sockets_switch_to_standby_while_reconnecting(monkeypatch: pytest.MonkeyPatch):
    """真 RouteSocket + 本地两台服务端：探测出备用线路，主线路断了就切过去。"""
    monkeypatch.setattr(route_group, "RouteSocket", route_mod.RouteSocket)
    monkeypatch.setattr(route_group, "RECONNECT_DELAYS", (0.2,) * 6)
    monkeypatch.setattr(route_group, "RECONNECT_SWITCH_AFTER", 0)
    monkeypatch.setattr(route_group, "MAIN_SETTLE_SECONDS", 0)
    monkeypatch.setattr(route_group, "STANDBY_SETTLE_SECONDS", 0)
    monkeypatch.setattr(route_mod, "DETECT_FIRST_PING", 0.01)
    monkeypatch.setattr(route_mod, "DETECT_PING_GAP", 0.05)

    log: list[tuple[str, str, int]] = []
    main_conns: list[ServerConnection] = []
    replies = {
        ".lq.Route.requestConnection": liblq.ResRequestConnection(result=1),
        ".lq.Route.heartbeat": liblq.ResHeartbeat(),
        ".lq.Lobby.prepareLogin": liblq.ResCommon(),
        ".lq.Route.requestRouteChange": liblq.ResRequestRouteChange(result=1),
        ".lq.Lobby.fastLogin": liblq.ResFastLogin(),
    }

    def handler_for(name: str):
        async def handler(ws: ServerConnection):
            if name == "A":
                if main_conns:
                    # 主线路断了以后一直连不上
                    await ws.close()
                    return
                main_conns.append(ws)
            async for raw in ws:
                assert isinstance(raw, bytes)
                index = raw[1] | (raw[2] << 8)
                wrapper = liblq.Wrapper().parse(raw[3:])
                kind = 0
                if wrapper.name == ".lq.Route.requestConnection":
                    kind = liblq.ReqRequestConnection().parse(wrapper.data).type
                elif wrapper.name == ".lq.Route.requestRouteChange":
                    kind = liblq.ReqRequestRouteChange().parse(wrapper.data).type
                log.append((name, wrapper.name, kind))
                if wrapper.name in replies:
                    body = bytes(liblq.Wrapper(name="", data=bytes(replies[wrapper.name])))
                    await ws.send(struct.pack("<BH", 3, index) + body)

        return handler

    async def scenario():
        async with serve(handler_for("A"), "127.0.0.1", 0) as srv_a, serve(handler_for("B"), "127.0.0.1", 0) as srv_b:
            port_a = list(srv_a.sockets)[0].getsockname()[1]
            port_b = list(srv_b.sockets)[0].getsockname()[1]
            routes = [
                model.MajsoulRoute(id="A", domain=f"127.0.0.1:{port_a}", ssl=False, level=1),
                model.MajsoulRoute(id="B", domain=f"127.0.0.1:{port_b}", ssl=False),
            ]
            listener = FakeListener()
            group = route_group.RouteGroup(constants.REGION_CN, FakeGateway(routes), listener)
            await group.open_entrance("")
            group.closed = False
            group._tick_detect(time.monotonic())
            await run_jobs(group)
            standby = group.standby
            # 等测速的几个 ping 回来，备用线路有了质量
            await asyncio.sleep(1.2)
            standby_quality = standby.quality() if standby is not None else constants.DELAY_INF
            await main_conns[0].close()
            await asyncio.sleep(0.05)
            reconnecting = group.reconnecting
            group._tick_black_hole(time.monotonic())
            await run_jobs(group)
            result = (group.main is standby, group.reconnecting, list(listener.reconnected), reconnecting)
            await group.close()
            return standby_quality, result

    standby_quality, (switched, still_reconnecting, reconnected, was_reconnecting) = asyncio.run(scenario())
    assert standby_quality < constants.DELAY_BAD_THRESHOLD
    assert was_reconnecting and switched and not still_reconnecting
    assert reconnected == [True]
    assert ("A", ".lq.Route.requestConnection", MAIN) in log
    assert ("B", ".lq.Route.requestConnection", STANDBY) in log
    assert ("B", ".lq.Lobby.prepareLogin", 0) in log
    b_calls = [name for host, name, _ in log if host == "B" and name != ".lq.Route.heartbeat"]
    assert b_calls[-2:] == [".lq.Route.requestRouteChange", ".lq.Lobby.fastLogin"]
    assert ("B", ".lq.Route.requestRouteChange", MAIN) in log
