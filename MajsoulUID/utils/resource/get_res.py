from io import BytesIO
from pathlib import Path

import httpx
from gsuid_core.logger import logger
from PIL import Image, UnidentifiedImageError


async def get_charactor_img(url: str, path: Path):
    sess = httpx.AsyncClient(verify=False)
    path.parent.mkdir(parents=True, exist_ok=True)

    response = None
    for _ in range(3):
        try:
            response = await sess.get(url)
        except (httpx.ReadTimeout, httpx.ConnectError, httpx.ConnectTimeout):
            logger.warning("[Majs][资源下载] 超时，重试一次...")
            continue
        break
    else:
        if response is None:
            logger.error(f"[Majs][资源下载] 超时，放弃下载： {url}")
            return Image.new("RGBA", (256, 256))

    if response.status_code == 200:
        byte_array = response.content
        try:
            _byte_array = bytes([(73 ^ byte) for byte in byte_array])
            buffer = BytesIO(_byte_array)
            image = Image.open(buffer)
        except UnidentifiedImageError:
            image = Image.open(BytesIO(byte_array))
        image.save(path)
        return image
    else:
        logger.error(f"[Majs][资源下载] 下载失败： {url}")
        return Image.new("RGBA", (256, 256))
