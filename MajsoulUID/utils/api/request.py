import datetime
from typing import TypeVar
from collections.abc import Mapping, Sequence

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

    def __init__(self) -> None:
        # 复用连接：每次新建客户端都要重新握手，单个请求实测多花约 0.7s。
        # 牌谱屋强制 cap token，不带会 429；求解与失效重试都在 cap_auth 里
        self._client = AsyncClient(auth=cap_auth, timeout=300, limits=Limits(keepalive_expiry=60))

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
        return await self._get_list(url, params, _RECORDS)

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
        # 对局少的号会缺字段（见 ac7f147），先垫上全 0 默认值再校验
        return _validate(adapter, {**defaults, **raw})

    async def _get_list(self, url: str, params: Params, adapter: TypeAdapter[list[T]]) -> list[T] | int:
        raw = await self._koromo_get(url, params)
        if not isinstance(raw, list):
            # 牌谱屋的错误 JSON 也是 dict，当成列表会在绘图时炸
            return raw if isinstance(raw, int) else -1
        return _validate(adapter, raw)

    async def _koromo_get(self, url: str, params: Params) -> Json | int:
        resp = await self._client.get(url, params=params)
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
