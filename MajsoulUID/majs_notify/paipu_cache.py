import json

import aiofiles

from ..utils.resource.RESOURCE_PATH import PAIPU_PATH

PAIPU_CACHE_VERSION = 1


async def get_paipu_by_game_id(game_id: str) -> dict | None:
    path = PAIPU_PATH / f"{game_id} - raw.json"
    if path.exists():
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            data = json.loads(await f.read())
            if "_parser_version" not in data or data["_parser_version"] != PAIPU_CACHE_VERSION:
                return None
            return data
    return None
