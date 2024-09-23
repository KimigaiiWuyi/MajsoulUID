import hashlib
import random
from pathlib import Path

from httpx import AsyncClient

from .constants import HEADERS, URL_BASE

HTTPX_CLIENT = AsyncClient(headers=HEADERS)


def encodeAccountId(account_id: int) -> int:
    return 1358437 + ((7 * account_id + 1117113) ^ 86216345)


async def getRes(path: str, bust_cache: bool = False):
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
    with open(cache_file, "wb") as f:
        f.write(resp.content)
        return resp.json()
