"""牌谱屋 cap token：自动求解 cap-widget format 2 挑战，以 httpx2 Auth 挂到 API 请求上。

牌谱屋 2026-07 起对 API 强制 cap token，不带 token 的请求返回 429 [x-cap-token-required]。
1. POST /challenge 下发若干 hashwx（PoW）题和一道 instrumentation（浏览器算术）题
2. hashwx 跑官方 wasm（见 hashwx.py）；instrumentation 把 blob 里的算术转译成 Python 求值
3. POST /redeem 按原顺序提交解，换到 token，之后以 Authorization: Bearer 携带
"""

import re
import ast
import time
import zlib
import base64
import asyncio
import operator
from collections.abc import AsyncGenerator
from typing import TypeVar, Callable, Annotated

import httpx2
import msgspec
from gsuid_core.logger import logger
from gsuid_core.server import on_core_start

from .hashwx import HashwxError, solve_hashwx

T = TypeVar("T")

CAP_API = "https://5-data.amae-koromo.com/cap/14f343ec68"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
# challenge 端点按 UA / Origin 过滤，不带浏览器头会返回 {"error":"Blocked"}
_CAP_HEADERS = {
    "accept": "*/*",
    "origin": "https://amae-koromo.sapk.ch",
    "referer": "https://amae-koromo.sapk.ch/",
    "user-agent": _UA,
}
# token 有效期约 2 小时：剩 30 分钟起在后台换新，剩 1 分钟起不再发出去
_REFRESH_AHEAD = 1800.0
_EXPIRY_MARGIN = 60.0


class CapError(Exception):
    """cap 响应不识别，或 blob 无法转译。"""


class HashwxPayload(msgspec.Struct):
    c: Annotated[str, msgspec.Meta(pattern="^[0-9a-f]{64}$")]
    d: Annotated[int, msgspec.Meta(gt=0)]
    n: Annotated[int, msgspec.Meta(gt=0)]


class HashwxChallenge(msgspec.Struct, tag_field="protocol", tag="hashwx"):
    payload: HashwxPayload


class InstrPayload(msgspec.Struct):
    blob: str


class InstrChallenge(
    msgspec.Struct, tag_field="protocol", tag="instrumentation"
):
    payload: InstrPayload


class ChallengeResp(msgspec.Struct):
    token: str
    challenges: list[HashwxChallenge | InstrChallenge]


class NonceSolution(msgspec.Struct):
    nonce: str


class Instr(msgspec.Struct):
    i: str
    state: dict[str, int]
    p: msgspec.Raw


class InstrSolution(msgspec.Struct):
    instr: Instr


class RedeemReq(msgspec.Struct):
    token: str
    solutions: list[NonceSolution | InstrSolution]


class RedeemResp(msgspec.Struct):
    token: str
    expires: float  # 毫秒时间戳


# blob 测量 17 种字体的渲染宽度，保留两位小数
# fmt: off
_FONT_WIDTHS = [
    610.44, 626.18, 588.63, 612.25, 634.09, 590.87, 621.32, 640.55, 618.41,
    605.76, 629.93, 615.28, 596.62, 623.77, 601.19, 633.5, 608.02,
]
# fmt: on

# 站点开了 blockAutomatedBrowsers，不带探针会被拒（instr_probe_missing）；探针只做启发式检测，
# 这里报一套自洽的桌面 Chrome：webdriver 为 false、窗口不超屏幕、没有 headless 标记
_CHROME_PROBE = msgspec.Raw(
    msgspec.json.encode(
        {
            "ua": _UA,
            "productSub": "20030107",
            "webdriver": False,
            "oscpu": "__undefined",
            "deviceMemory": 8,
            "uaDataPresent": True,
            "uaData": {
                "mobile": False,
                "brands": [
                    "Not-A.Brand/99",
                    "Google Chrome/157",
                    "Chromium/157",
                ],
            },
            "plugins": {"length": 5},
            "pdfViewerEnabled": True,
            "engine": {"hasMozInnerScreenX": False, "hasChrome": True},
            "screen": {"width": 1920, "height": 1080},
            "outerWH": [1920, 1040],
            "isExtended": False,
            "fontWidths": _FONT_WIDTHS,
            "tamper": {
                "getParameterWebGL": True,
                "toDataURL": True,
                "getImageData": True,
                "permissionsQuery": True,
                "fnToString": True,
            },
        }
    )
)


# instrumentation blob（deflate-raw + base64）是一段 JS：4 个变量经约 20 条位运算赋值后输出。
# 等式部分恰好是合法的 Python 语法，交给 ast 解析后按 JS 语义求值
Helper = Callable[[int, int, int], int]

_ID = r"[A-Za-z0-9_$]+"
_CHALLENGE_ID = re.compile(r'nonce:"([0-9a-f]{32})"')
# 形如 var o={};o["a"]=a;o["b"]=b;…return o;
_STATE_OUT = re.compile(
    rf'var ({_ID})=\{{\}};((?:\1\["{_ID}"\]={_ID};)+)return \1;'
)
_STATE_KEY = re.compile(rf'\["({_ID})"\]')
# 请求总带 UA，三元恒取真分支；换掉它之后剩下的语法 Python 都认
_UA_TERNARY = re.compile(r"navigator\.userAgent \? (\d+) : \d+")
_BITWISE: dict[type[ast.operator], Callable[[int, int], int]] = {
    ast.BitXor: operator.xor,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
}


def _to_int32(v: int) -> int:
    return (v + (1 << 31)) % (1 << 32) - (1 << 31)


def _js_mod(a: int, b: int) -> int:
    r = abs(a) % abs(b)
    return r if a >= 0 else -r


def _fn_helper(a: int, b: int, c: int) -> int:
    # new F(a) 读自身的 k=b；改原型后 new F(b) 读原型上的 k=c
    return (b ^ a) | (c ^ b)


def _dom_sum(x: int, y: int, z: int) -> int:
    # 建树时每个值右移 8 次，当前位为 0 的中间值进链；回溯求和就是把这些值加起来
    return _js_mod(
        sum(v >> i for v in (x, y, z) for i in range(8) if (v >> i) & 1 == 0),
        256,
    )


# blob 每次换函数名，按定义里的特征串认出语义
_HELPER_MARKERS: dict[str, Helper] = {
    "this.k^d": _fn_helper,
    "document.createElement": _dom_sum,
}


def _bind_helpers(src: str, names: set[str]) -> dict[str, Helper]:
    """按定义位置把 helper 区切成每个函数一段，凭段里的特征串认出语义。"""
    starts = sorted((src.find(f"function {name}("), name) for name in names)
    if any(pos < 0 for pos, _ in starts):
        raise CapError("blob 等式调用了未定义的函数")
    ends = [pos for pos, _ in starts[1:]] + [len(src)]
    bound: dict[str, Helper] = {}
    for (start, name), end in zip(starts, ends):
        impls = [
            impl
            for marker, impl in _HELPER_MARKERS.items()
            if marker in src[start:end]
        ]
        if len(impls) != 1:
            raise CapError(f"blob 里认不出 helper 函数 {name}")
        bound[name] = impls[0]
    return bound


def _js_eval(
    node: ast.expr, state: dict[str, int], helpers: dict[str, Helper]
) -> int:
    """位运算两侧先截成 int32，% 的符号跟被除数，与 JS 一致；不认识的节点直接报错。"""

    def ev(sub: ast.expr) -> int:
        return _js_eval(sub, state, helpers)

    match node:
        case ast.Constant(value=int() as value):
            return value
        case ast.Name(id=name) if name in state:
            return state[name]
        case ast.UnaryOp(op=ast.Invert(), operand=operand):
            return ~_to_int32(ev(operand))
        case ast.BinOp(left=left, op=op, right=right) if type(op) in _BITWISE:
            return _BITWISE[type(op)](
                _to_int32(ev(left)), _to_int32(ev(right))
            )
        case ast.BinOp(left=left, op=ast.Mod(), right=right):
            return _js_mod(ev(left), ev(right))
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            return ev(left) + ev(right)
        case ast.Call(func=ast.Name(id=fn), args=[a, b, c]) if fn in helpers:
            return helpers[fn](
                _to_int32(ev(a)), _to_int32(ev(b)), _to_int32(ev(c))
            )
    raise CapError(f"blob 表达式无法转译: {ast.unparse(node)[:80]}")


def _solve_instrumentation(blob: str) -> Instr:
    raw = zlib.decompress(base64.b64decode(blob), -15).decode("utf-8")
    challenge_id = _CHALLENGE_ID.search(raw)
    out = _STATE_OUT.search(raw)
    if challenge_id is None or out is None:
        raise CapError("blob 里找不到挑战 id 或 state 输出对象")

    # 输出对象的键就是 4 个状态变量名，state 按这些键上报
    names: list[str] = _STATE_KEY.findall(out.group(2))
    state: dict[str, int] = {}
    decl_end = 0
    for name in names:
        decl = re.search(rf"var {re.escape(name)}=(-?\d+);", raw)
        if decl is None:
            raise CapError(f"blob 里找不到 {name} 的初值")
        state[name] = int(decl.group(1))
        decl_end = max(decl_end, decl.end())

    # 声明之后先是两个 helper 定义，再是等式；等式里没有花括号，最后一个 } 就是分界
    body = raw[decl_end : out.start()]
    cut = body.rfind("}") + 1
    try:
        tree = ast.parse(_UA_TERNARY.sub(r"\1", body[cut:]).strip())
    except SyntaxError as e:
        raise CapError(f"blob 等式不是可识别的语法: {e}") from e
    calls = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    helpers = _bind_helpers(body[:cut], calls)
    for stmt in tree.body:
        match stmt:
            case ast.Assign(targets=[ast.Name(id=dest)], value=value) if (
                dest in state
            ):
                state[dest] = _js_eval(value, state, helpers)
            case _:
                raise CapError(f"blob 语句无法转译: {ast.unparse(stmt)[:80]}")
    return Instr(
        i=challenge_id.group(1),
        state={name: state[name] for name in names},
        p=_CHROME_PROBE,
    )


def _decode(resp: httpx2.Response, type_: type[T]) -> T:
    try:
        return msgspec.json.decode(resp.content, type=type_)
    except msgspec.DecodeError as e:
        raise CapError(
            f"{resp.url.path} 响应不识别 (HTTP {resp.status_code}): {resp.text[:120]!r}"
        ) from e


async def _solve(
    challenge: HashwxChallenge | InstrChallenge,
) -> NonceSolution | InstrSolution:
    if isinstance(challenge, InstrChallenge):
        return InstrSolution(_solve_instrumentation(challenge.payload.blob))
    p = challenge.payload
    return NonceSolution(str(await solve_hashwx(p.c, p.d, p.n)))


async def _fetch_token() -> tuple[str, float]:
    """跑一轮 challenge -> 求解 -> redeem，返回 (token, 过期时间戳秒)。"""
    start = time.perf_counter()
    async with httpx2.AsyncClient(headers=_CAP_HEADERS, timeout=30) as client:
        challenge = _decode(
            await client.post(f"{CAP_API}/challenge"), ChallengeResp
        )
        # gather 保持题目原顺序，redeem 按下标逐一校验
        solutions = await asyncio.gather(
            *(_solve(ch) for ch in challenge.challenges)
        )
        resp = await client.post(
            f"{CAP_API}/redeem",
            content=msgspec.json.encode(RedeemReq(challenge.token, solutions)),
            headers={"content-type": "application/json"},
        )
        redeemed = _decode(resp, RedeemResp)
    hashes = sum(
        int(s.nonce) + 1 for s in solutions if isinstance(s, NonceSolution)
    )
    logger.info(
        f"[MajsoulUID] 牌谱屋 cap token 已刷新"
        f"（{len(solutions)} 题，{hashes} 次哈希，{time.perf_counter() - start:.1f}s）"
    )
    return redeemed.token, redeemed.expires / 1000


def _with_token(request: httpx2.Request, token: str | None) -> httpx2.Request:
    if token is not None:
        request.headers["authorization"] = f"Bearer {token}"
    return request


class KoromoCapAuth(httpx2.Auth):
    """给牌谱屋 API 请求挂 cap token；token 被拒就换新的重试一次。

    快到期时照用旧 token、后台换新的，请求不用等 PoW。
    求解失败时不挂 token 照常请求，429 交给调用方处理。
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._background: asyncio.Task[None] | None = None

    def refresh_in_background(self) -> None:
        if self._background is None or self._background.done():
            self._background = asyncio.create_task(self._refresh())

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        token = await self._get_token()
        response = yield _with_token(request, token)
        if response.status_code != 429:
            return
        # requires_response_body 只在基类的 flow 里生效，覆写后得自己读
        await response.aread()
        if "x-cap-token-required" not in response.text:
            return
        token = await self._get_token(rejected=token)
        if token is not None:
            # 和官方前端一样，换 token 后加时间戳参数绕开缓存
            request.url = request.url.copy_add_param(
                "cap_token_refreshed", int(time.time() * 1000)
            )
            yield _with_token(request, token)

    def _usable(self, rejected: str | None) -> bool:
        return (
            self._token is not None
            and self._token != rejected
            and time.time() < self._expires_at - _EXPIRY_MARGIN
        )

    def _fresh(self) -> bool:
        return (
            self._token is not None
            and time.time() < self._expires_at - _REFRESH_AHEAD
        )

    async def _get_token(self, rejected: str | None = None) -> str | None:
        """rejected 是服务端刚拒掉的 token。"""
        if not self._usable(rejected):
            await self._refresh(rejected)
        elif not self._fresh():
            self.refresh_in_background()
        return self._token if self._usable(rejected) else None

    async def _refresh(self, rejected: str | None = None) -> None:
        async with self._lock:
            # 排队等锁期间可能已经有人换好了，多个请求同时被拒也只刷新一次
            if self._fresh() and self._token != rejected:
                return
            try:
                self._token, self._expires_at = await _fetch_token()
            except (CapError, HashwxError, httpx2.HTTPError) as e:
                logger.warning(f"[MajsoulUID] 牌谱屋 cap 求解失败: {e!r}")


cap_auth = KoromoCapAuth()


@on_core_start
async def _prewarm_cap_token() -> None:
    # 启动时先在后台解一次，第一条查询不用等 PoW，也不拖慢启动
    cap_auth.refresh_in_background()
