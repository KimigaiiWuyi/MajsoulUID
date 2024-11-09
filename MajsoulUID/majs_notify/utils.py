import random
import hashlib
from typing import Dict
from pathlib import Path

import aiofiles
from httpx import AsyncClient

from .constants import HEADERS

HTTPX_CLIENT = AsyncClient(headers=HEADERS)


async def getRes(URL_BASE: str, path: str, bust_cache: bool = False) -> Dict:
    HTTPX_CLIENT.headers["Referer"] = URL_BASE

    url = (
        f"{URL_BASE}/1/{path}"
        if URL_BASE == "https://game.maj-soul.com/"
        else f"{URL_BASE}{path}"
    )

    cache_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
    if bust_cache:
        url += f"?randv={str(random.random())[2:]}"

    cache_dir = Path(".cache")
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / cache_hash

    resp = await HTTPX_CLIENT.get(url)
    resp.raise_for_status()
    async with aiofiles.open(cache_file, "wb") as f:
        await f.write(resp.content)
    return resp.json()
