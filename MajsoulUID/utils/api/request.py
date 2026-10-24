import datetime
from json import JSONDecodeError
from typing import Any, Dict, List, Union, Literal, Optional, cast

from httpx import AsyncClient

from gsuid_core.logger import logger

from .api import (
    KOROMO_PLAYER_STATS,
    KOROMO_PLAYER_EXTEND,
    KOROMO_PLAYER_RECORD,
    KOROMO_SEARCH_PLAYER,
)
from .models import Game, Stats, Player, Extended
from .remote_const import GameMode

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


class KoromoApi:
    ssl_verify = True
    _HEADER: Dict[str, str] = {}

    async def get_player_stats(
        self,
        player_id: Union[int, str],
        MODE: Union[str, int] = "4",
    ):
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        data = await self._koromo_request(
            KOROMO_PLAYER_STATS.format(MODE, player_id, timestamp),
            params={
                "mode": MODE_4 if MODE == "4" else MODE_3,
                "tag": "473317",
            },
        )
        if isinstance(data, Dict):
            return cast(Stats, data)
        return data

    async def get_player_extended(
        self,
        player_id: Union[int, str],
        MODE: Union[str, int] = "4",
    ):
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        data = await self._koromo_request(
            KOROMO_PLAYER_EXTEND.format(MODE, player_id, timestamp),
            params={
                "mode": MODE_4 if MODE == "4" else MODE_3,
                "tag": "473317",
            },
        )
        if isinstance(data, Dict):
            return cast(Extended, data)
        return data

    async def get_player_record(
        self,
        player_id: Union[int, str],
        limit: Union[int, str] = 16,
        MODE: Union[str, int] = "4",
    ) -> Union[List[Game], int]:
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        URL = KOROMO_PLAYER_RECORD.format(MODE, player_id, timestamp)
        data = await self._koromo_request(
            URL,
            params={
                "limit": limit,
                "mode": MODE_4 if MODE == "4" else MODE_3,
                "tag": "54",
                "descending": True,
            },
        )
        # 牌谱屋的错误 JSON 也是 dict，当成对局列表会在绘图时炸
        if isinstance(data, list):
            return data
        return data if isinstance(data, int) else -1

    async def search_player(
        self,
        player_name: str,
        num: int = 4,
        MODE: Union[str, int] = "4",
    ) -> List[Player]:
        data = await self._koromo_request(
            KOROMO_SEARCH_PLAYER.format(MODE, player_name),
            params={"limit": num, "tag": "all"},
        )
        if isinstance(data, list):
            return data
        return []

    async def _koromo_request(
        self,
        url: str,
        method: Literal["GET", "POST"] = "GET",
        header: Dict[str, str] = _HEADER,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Union[Dict, List, int]:
        async with AsyncClient() as client:
            resp = await client.request(
                method,
                url=url,
                headers=header,
                params=params,
                json=json,
                data=data,
                timeout=300,
            )
            try:
                raw_data = resp.json()
            except JSONDecodeError:
                # 限流 / 验证码页（HTTP 429 纯文本）走这里
                logger.warning(f"[MajsoulUID] 牌谱屋返回非 JSON (HTTP {resp.status_code}): {resp.text[:200]}")
                return -999
            logger.debug(raw_data)
            if "error" in raw_data:
                return -1
            return raw_data
