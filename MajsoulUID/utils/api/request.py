import datetime
from typing import Any, Dict, List, Union, Literal, Optional, cast

from gsuid_core.logger import logger
from aiohttp import FormData, TCPConnector, ClientSession, ContentTypeError

from .remote_const import GameMode
from .models import Game, Stats, Player, Extended
from .api import (
    KOROMO_PLAYER_STATS,
    KOROMO_PLAYER_EXTEND,
    KOROMO_PLAYER_RECORD,
    KOROMO_SEARCH_PLAYER,
)

MODE_MAP = {
    '四王座': 16,
    '四玉': 12,
    '四金': 9,
    '四玉东': 11,
    '四金东': 8,
    '三王座': 26,
    '三金': 22,
    '三金东': 21,
    '三玉': 24,
    '三玉东': 23,
}

MODE_3 = ','.join(str(mode.value) for mode in GameMode if '三' in mode.name)
MODE_4 = ','.join(str(mode.value) for mode in GameMode if '三' not in mode.name)


class KoromoApi:
    ssl_verify = True
    _HEADER: Dict[str, str] = {}

    async def get_player_stats(
        self,
        player_id: Union[int, str],
        MODE: Union[str, int] = '4',
    ):
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        data = await self._koromo_request(
            KOROMO_PLAYER_STATS.format(MODE, player_id, timestamp),
            params={
                'mode': MODE_4 if MODE == '4' else MODE_3,
                'tag': '473317',
            },
        )
        if isinstance(data, Dict):
            return cast(Stats, data)
        return data

    async def get_player_extended(
        self,
        player_id: Union[int, str],
        MODE: Union[str, int] = '4',
    ):
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        data = await self._koromo_request(
            KOROMO_PLAYER_EXTEND.format(MODE, player_id, timestamp),
            params={
                'mode': MODE_4 if MODE == '4' else MODE_3,
                'tag': '473317',
            },
        )
        if isinstance(data, Dict):
            return cast(Extended, data)
        return data

    async def get_player_record(
        self,
        player_id: Union[int, str],
        limit: Union[int, str] = 16,
        MODE: Union[str, int] = '4',
    ):
        timestamp = int(datetime.datetime.now().timestamp() * 1000)
        data = await self._koromo_request(
            KOROMO_PLAYER_RECORD.format(MODE, player_id, timestamp),
            params={
                'limit': str(limit),
                'mode': MODE_4 if MODE == '4' else MODE_3,
                'tag': '466',
            },
        )
        if isinstance(data, List) or isinstance(data, Dict):
            return cast(List[Game], data)
        return data

    async def search_player(
        self,
        player_name: str,
        num: int = 4,
        MODE: Union[str, int] = '4',
    ):
        data = await self._koromo_request(
            KOROMO_SEARCH_PLAYER.format(MODE, player_name),
            params={'limit': num, 'tag': 'all'},
        )
        return cast(List[Player], data)

    async def _koromo_request(
        self,
        url: str,
        method: Literal["GET", "POST"] = "GET",
        header: Dict[str, str] = _HEADER,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[FormData] = None,
    ) -> Union[Dict, int]:
        async with ClientSession(
            connector=TCPConnector(verify_ssl=self.ssl_verify)
        ) as client:
            async with client.request(
                method,
                url=url,
                headers=header,
                params=params,
                json=json,
                data=data,
                timeout=300,
            ) as resp:
                try:
                    raw_data = await resp.json()
                except ContentTypeError:
                    _raw_data = await resp.text()
                    raw_data = {"retcode": -999, "data": _raw_data}
                logger.debug(raw_data)
                if 'error' in raw_data:
                    return -1
                return raw_data
