# https://github.com/Xerxes-2/AutoLiqi/blob/main/get_liqi.py
import json
import os
from pathlib import Path
from time import sleep
from typing import Dict

import httpx
from config import ConfigTables
from sheet import *  # noqa: F403,F401

Headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"  # noqa: E501
}

path = Path(__file__).parent


def get_version():
    req = httpx.get("https://game.maj-soul.com/1/version.json", headers=Headers)
    return req.json()


def get_prefix(version):
    req = httpx.get(
        f"https://game.maj-soul.com/1/resversion{version}.json",
        headers=Headers,
    )
    return req.json()["res"]["res/proto/liqi.json"]["prefix"]


def get_res_prefix(version):
    d = {}
    req = httpx.get(
        f"https://game.maj-soul.com/1/resversion{version}.json",
        headers=Headers,
    )
    res = req.json()["res"]
    for k in res:
        if "extendRes/charactor" in k:
            d[k] = res[k]["prefix"]

    with open(path / "extendRes.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(d, indent=4, ensure_ascii=False))


def get_lqc_prefix(version):
    req = httpx.get(
        f"https://game.maj-soul.com/1/resversion{version}.json",
        headers=Headers,
    )
    return req.json()["res"]["res/config/lqc.lqbin"]["prefix"]


def get_liqi(prefix):
    req = httpx.get(
        f"https://game.maj-soul.com/1/{prefix}/res/proto/liqi.json",
        headers=Headers,
    )
    return req.text


def get_lqc(prefix):
    lqc_path = path / "lqc.lqbin"
    if lqc_path.exists():
        print("已存在lqc.lqbin，删除后重新下载...")
        lqc_path.unlink()
    print("正在下载lqc.lqbin...")
    print(f"https://game.maj-soul.com/1/{prefix}/res/config/lqc.lqbin")
    req = httpx.get(
        f"https://game.maj-soul.com/1/{prefix}/res/config/lqc.lqbin",
        headers=Headers,
    )
    print("下载完成，正在保存lqc.lqbin...")
    with open(lqc_path, "wb") as f:
        f.write(req.content)
    return req.content


def get_code_js(code):
    req = httpx.get(f"https://game.maj-soul.com/1/{code}", headers=Headers)
    return req.text


def main():
    print("开始获取版本prefix")
    version = get_version()
    prefix = get_prefix(version["version"])
    print(prefix)

    print("开始获取Resprefix")
    sleep(1)
    get_res_prefix(version["version"])

    print("开始获取lqc.lqbin")
    if (path / "lqc.lqbin").exists():
        lqc_data = (path / "lqc.lqbin").read_bytes()
    else:
        lqc_data = get_lqc(get_lqc_prefix(version["version"]))
    load_lqc_lqbin(lqc_data)

    print("开始获取liqi")
    liqi = get_liqi(prefix)
    with open("liqi.json", "w") as f:
        f.write(liqi)
    env = os.getenv("GITHUB_ENV")
    with open(env, "a") as f:  # type:ignore
        f.write(f"liqi-json={prefix}\n")


def to_camel_case(snake_str: str):
    components = snake_str.split("_")
    return "".join(x.capitalize() for x in components)


def contains_bytes(data: Dict):
    for value in data.values():
        if isinstance(value, bytes):
            return True
        elif isinstance(value, dict):
            if contains_bytes(value):
                return True
    return False


def load_lqc_lqbin(lqc_lqbin: bytes):
    lqc = {}
    _id_to_skin = {}

    sd = ConfigTables().parse(lqc_lqbin)
    n = [
        # 'ItemDefinitionCharacter',
        # 'CharacterSkin',
        # 'ChestChestShop',
        "ItemDefinitionSkin"
    ]

    for data in sd.datas:
        table_name = data.table
        sheet_name = data.sheet
        model_name = to_camel_case(table_name) + to_camel_case(sheet_name)
        print(model_name)

        if model_name not in n:
            continue

        if model_name in globals():
            cls = globals()[model_name]
        else:
            print(f"未找到{model_name}")
            continue

        for index, _d in enumerate(data.data):
            _unpack = cls().parse(_d)
            ud: Dict = _unpack.__dict__
            if "_unknown_fields" in ud:
                ud.pop("_unknown_fields")

            if model_name not in lqc:
                lqc[model_name] = []

            _id_to_skin[ud["id"]] = ud

            lqc[model_name].append(ud)

    """
    with open(path / "lqc.json", "w", encoding='utf-8') as f:
        f.write(json.dumps(lqc, indent=4, ensure_ascii=False))
    """

    with open(path / "lqc.json", "w", encoding="utf-8") as f:
        f.write(json.dumps(_id_to_skin, indent=4, ensure_ascii=False))


if __name__ == "__main__":
    main()
