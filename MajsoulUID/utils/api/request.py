import time
import asyncio
import datetime
from typing import TypeVar
from collections.abc import Mapping, Sequence

import httpx2
from httpx2 import Limits, AsyncClient
from pydantic import JsonValue, TypeAdapter, ValidationError

from gsuid_core.logger import logger

from .api import (
    KOROMO_PLAYER_STATS,
    KOROMO_PLAYER_EXTEND,
    KOROMO_PLAYER_RECORD,
    KOROMO_SEARCH_PLAYER,
)
from .models import Game, Stats, Player, Extended
from .cap_solver import cap_auth
from .remote_const import GameMode, player_stats_zero, player_extend_zero
from ...majs_config.majs_config import MAJS_CONFIG

T = TypeVar("T")
Params = dict[str, str | int | bool]
Json = dict[str, JsonValue] | list[JsonValue]

MODE_MAP = {
    "四王座": 16,
    "四玉": 12,
    "四金": 9,
    "四玉东": 11,
    "四金东": 8,
    "三王座": 26,
    "三金": 22,
    "三金东": 21,
    "三玉": 24,
    "三玉东": 23,
}

MODE_3 = ",".join(str(mode.value) for mode in GameMode if "三" in mode.name)
MODE_4 = ",".join(str(mode.value) for mode in GameMode if "三" not in mode.name)

# msgspec 校验中文键的 TypedDict 会误报缺字段（读字段名 UTF-8 缓存却不先填），这里用 pydantic
_JSON: TypeAdapter[Json] = TypeAdapter(Json)
_STATS = TypeAdapter(Stats)
_EXTENDED = TypeAdapter(Extended)
_RECORDS = TypeAdapter(list[Game])
_PLAYERS = TypeAdapter(list[Player])


def _now_ms() -> int:
    return int(datetime.datetime.now().timestamp() * 1000)


def _validate(adapter: TypeAdapter[T], raw: Mapping[str, object] | Sequence[JsonValue]) -> T | int:
    try:
        return adapter.validate_python(raw)
    except ValidationError as e:
        logger.warning(f"[MajsoulUID] 牌谱屋返回的结构对不上: {e}")
        return -1


class KoromoApi:
    ssl_verify = True
    # 牌谱屋公共限额要求 QPS≈1（三/四麻分开计量），见 amae-koromo#126
    _MIN_INTERVAL = 1.0

    def __init__(self) -> None:
        # 复用连接：每次新建客户端都要重新握手，单个请求实测多花约 0.7s。
        # 查询类端点匿名放行（带 cap token 反而被拒）；records 走 cap token；
        # 配了牌谱屋 API Key 时以 Bearer 携带，覆盖匿名配额
        self._client = AsyncClient(timeout=300, limits=Limits(keepalive_expiry=60))
        api_key: str = MAJS_CONFIG.get_config("MajsKoromoApiKey").data
        if api_key:
            self._client.headers["authorization"] = f"Bearer {api_key}"
        self._pace_lock = asyncio.Lock()
        self._last_request = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_player_stats(self, player_id: int | str, MODE: int | str = "4") -> Stats | int:
        url = KOROMO_PLAYER_STATS.format(MODE, player_id, _now_ms())
        params: Params = {"mode": MODE_4 if MODE == "4" else MODE_3, "tag": "473317"}
        return await self._get_object(url, params, _STATS, player_stats_zero)

    async def get_player_extended(self, player_id: int | str, MODE: int | str = "4") -> Extended | int:
        url = KOROMO_PLAYER_EXTEND.format(MODE, player_id, _now_ms())
        params: Params = {"mode": MODE_4 if MODE == "4" else MODE_3, "tag": "473317"}
        return await self._get_object(url, params, _EXTENDED, player_extend_zero)

    async def get_player_record(
        self,
        player_id: int | str,
        limit: int | str = 16,
        MODE: int | str = "4",
    ) -> list[Game] | int:
        url = KOROMO_PLAYER_RECORD.format(MODE, player_id, _now_ms())
        params: Params = {
            "limit": limit,
            "mode": MODE_4 if MODE == "4" else MODE_3,
            "tag": "54",
            "descending": True,
        }
        return await self._get_list(url, params, _RECORDS, use_cap=True)

    async def search_player(self, player_name: str, num: int = 4, MODE: int | str = "4") -> list[Player]:
        players = await self._get_list(
            KOROMO_SEARCH_PLAYER.format(MODE, player_name), {"limit": num, "tag": "all"}, _PLAYERS
        )
        return players if isinstance(players, list) else []

    async def _get_object(
        self, url: str, params: Params, adapter: TypeAdapter[T], defaults: Mapping[str, object]
    ) -> T | int:
        raw = await self._koromo_get(url, params)
        if not isinstance(raw, dict):
            return raw if isinstance(raw, int) else -1
        # 对局少的号会缺字段（见 ac7f147），先垫上全 0 默认值再校验；
        # 服务器对无数据字段会显式回 null，垫底前先剥掉
        present = {k: v for k, v in raw.items() if v is not None}
        return _validate(adapter, {**defaults, **present})

    async def _get_list(
        self, url: str, params: Params, adapter: TypeAdapter[list[T]], use_cap: bool = False
    ) -> list[T] | int:
        raw = await self._koromo_get(url, params, use_cap)
        if not isinstance(raw, list):
            # 牌谱屋的错误 JSON 也是 dict，当成列表会在绘图时炸
            return raw if isinstance(raw, int) else -1
        return _validate(adapter, raw)

    async def _koromo_get(self, url: str, params: Params, use_cap: bool = False) -> Json | int:
        # 调用方会一次 gather 四个查询，串行加间隔把突发压回 QPS≈1
        async with self._pace_lock:
            delay = self._MIN_INTERVAL - (time.monotonic() - self._last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request = time.monotonic()
            resp = await self._get_with_cap(url, params) if use_cap else await self._client.get(url, params=params)
        try:
            raw = _JSON.validate_json(resp.content)
        except ValidationError:
            # 限流 / 验证码页（HTTP 429 纯文本）走这里
            logger.warning(f"[MajsoulUID] 牌谱屋返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}")
            return -999
        logger.debug(raw)
        if isinstance(raw, dict) and "error" in raw:
            return -1
        return raw

    async def _get_with_cap(self, url: str, params: Params) -> httpx2.Response:
        """records 专用：挂 cap token，被拒就换新 token 重试一次（官方前端同款）。"""
        token = await cap_auth.get_token()
        if token is None:
            # 求解失败时裸请求照发，429 交给上层处理
            return await self._client.get(url, params=params)
        headers = {"authorization": f"Bearer {token}"}
        resp = await self._client.get(url, params=params, headers=headers)
        if resp.status_code != 429 or "x-cap-token-required" not in resp.text:
            return resp
        fresh = await cap_auth.get_token(rejected=token)
        if fresh is None:
            return resp
        # 换 token 后加时间戳参数绕缓存，与官方前端一致
        refreshed = {**params, "cap_token_refreshed": int(time.time() * 1000)}
        return await self._client.get(url, params=refreshed, headers={"authorization": f"Bearer {fresh}"})
        try:
            raw = _JSON.validate_json(resp.content)
        except ValidationError:
            # 限流 / 验证码页（HTTP 429 纯文本）走这里
            logger.warning(f"[MajsoulUID] 牌谱屋返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}")
            return -999
        logger.debug(raw)
        if isinstance(raw, dict) and "error" in raw:
            return -1
        return raw
