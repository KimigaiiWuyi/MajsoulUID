"""线路相关测试共用的假对象：不连真网络，按方法名回包，线路质量可以手动设。"""

import sys
import time
import types
import importlib
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "MajsoulUID"
PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"

# majs_notify/__init__.py 会拉起数据库与定时任务，这里只占位包
if f"{PKG}.majs_notify" not in sys.modules:
    stub = types.ModuleType(f"{PKG}.majs_notify")
    stub.__path__ = [str(PLUGIN_ROOT / "majs_notify")]
    sys.modules[f"{PKG}.majs_notify"] = stub

route_mod = importlib.import_module(f"{PKG}.majs_notify.route")
model = importlib.import_module(f"{PKG}.majs_notify.model")
constants = importlib.import_module(f"{PKG}.majs_notify.constants")
liblq = importlib.import_module(f"{PKG}.lib.lq")

DEFAULT_QUALITY = 0.05


def make_route(rid: str, level: int = 0, state: str = "idle"):
    return model.MajsoulRoute(id=rid, domain=f"{rid}.invalid", ssl=True, state=state, level=level)


class FakeServer:
    """按方法名排好回包；回包可以是函数，拿到 (socket, 请求) 再决定返回什么。"""

    def __init__(self) -> None:
        self.script: dict[str, list] = {}
        self.calls: list[str] = []
        self.requests: dict[str, list] = {}
        self.sent: list[str] = []
        self.sockets: list["FakeSocket"] = []
        self.quality: dict[str, float] = {}
        self.reject: set[str] = set()
        self.fail_opens = 0
        self.opens = 0

    def reply(self, method: str, *responses) -> None:
        service = "Route" if method in ("requestRouteChange", "heartbeat") else "Lobby"
        self.script.setdefault(f".lq.{service}.{method}", []).extend(responses)


class FakeSocket:
    server: FakeServer

    def __init__(self, info, region, route_type, on_notify, on_close) -> None:
        self.info = info
        self.route = info.route
        self.route_type = route_type
        self.on_notify = on_notify
        self.on_close = on_close
        self.locked = False
        self.during_detect = False
        self.opened_at = route_mod.NEVER
        self.usable = False
        self.closed = False
        self.detects = 0
        self.waiting_rpc_count = 0
        self.server.sockets.append(self)

    def is_open(self) -> bool:
        return self.usable and not self.closed

    def quality(self) -> float:
        if not self.is_open():
            return constants.DELAY_INF
        rid = self.route.id
        self.info.last_quality = self.server.quality[rid] if rid in self.server.quality else DEFAULT_QUALITY
        return self.info.last_quality

    def delay(self) -> float:
        return self.quality()

    def need_detect(self) -> bool:
        return time.monotonic() > self.info.detect_end + constants.DETECT_INTERVAL

    def detect(self) -> None:
        # 测试里测速瞬间完成
        self.detects += 1
        self.info.detect_end = time.monotonic()
        self.quality()

    async def open(self) -> None:
        self.server.opens += 1
        self.info.connected = True
        if self.server.fail_opens:
            self.server.fail_opens -= 1
            raise ConnectionError("refused")
        if self.route.id in self.server.reject:
            raise route_mod.RouteRejected("rejected")
        self.opened_at = time.monotonic()
        self.usable = True

    async def close(self) -> None:
        self.closed = True

    def drop(self) -> None:
        """模拟服务端断开。"""
        self.closed = True
        self.on_close(self)

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


class FakeGateway:
    def __init__(self, routes) -> None:
        self.routes = {route.id: route for route in routes}
        self.maintenance = ""
        self.on_routes = None
        self.checks = 0

    async def fetch(self) -> bool:
        return True

    def check_fetch(self, force: bool = False) -> bool:
        self.checks += 1
        return False


class FakeListener:
    def __init__(self) -> None:
        self.notices: list[str] = []
        self.reconnected: list[bool] = []
        self.failed: list[str] = []
        self.started = 0

    def on_group_notify(self, socket, notify) -> None:
        self.notices.append(notify.method_name)

    def on_reconnect_started(self) -> None:
        self.started += 1

    async def on_reconnected(self, changed: bool) -> None:
        self.reconnected.append(changed)

    async def on_reconnect_failed(self, reason: str) -> None:
        self.failed.append(reason)

    def prepare_login_request(self):
        return liblq.ReqPrepareLogin(access_token="tok", type=0)
