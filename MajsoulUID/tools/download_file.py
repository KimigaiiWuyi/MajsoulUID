import json
from io import BytesIO
from typing import Dict
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

BASE = "https://game.maj-soul.com/1/"
RESOURCE = Path(__file__).parent / "resource"

with open(Path(__file__).parent / "resource.json", "r") as f:
    raw_data = json.loads(f.read())

if (Path(__file__).parent / "reslut.json").exists():
    with open(
        Path(__file__).parent / "reslut.json", "r", encoding="utf8"
    ) as f:
        result = json.loads(f.read())
else:
    result = {}

if not RESOURCE.exists():
    RESOURCE.mkdir()

resource: Dict[str, Dict[str, str]] = raw_data["res"]

try:
    for res in resource:
        if "charactor" not in res or ".png" not in res:
            continue

        path = RESOURCE.joinpath(res)

        if not path.parent.exists():
            path.parent.mkdir(parents=True)

        if path.exists():
            print(f"已存在，跳过 {path}")
            continue

        if res in result and result[res] == "请求失败":
            print(f"会失败，跳过 {path}")
            continue

        url = f"{BASE}{resource[res]['prefix']}/{res}"

        print(f"URL: {url}")
        print(f"正在下载至 {path}")

        response = None
        for i in range(3):
            try:
                response = httpx.get(url, verify=False)
            except httpx.ReadTimeout:
                print("超时，重试一次...")
                continue
            except (httpx.ConnectError, httpx.ConnectTimeout):
                print("SSL错误，重试一次...")
                response = 1
                continue
            break
        else:
            if response is None:
                result[res] = "请求失败"
                print(f"超时，放弃 {path}")
                continue
            elif response == 1:
                result[res] = "SSL错误"
                print(f"SSL错误，放弃 {path}")
                continue

        if response.status_code == 200:
            byte_array = response.content
            try:
                _byte_array = bytes([(73 ^ byte) for byte in byte_array])
                buffer = BytesIO(_byte_array)
                image = Image.open(buffer)
            except UnidentifiedImageError:
                image = Image.open(BytesIO(byte_array))
            image.save(path)
            result[res] = "成功！"
        else:
            result[res] = "请求失败"
            print("请求失败")
finally:
    with open(
        Path(__file__).parent / "result.json", "w", encoding="utf8"
    ) as f:
        json.dump(result, f, indent=4, ensure_ascii=False)
