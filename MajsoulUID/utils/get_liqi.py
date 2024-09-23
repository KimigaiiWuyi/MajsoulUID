# https://github.com/Xerxes-2/AutoLiqi/blob/main/get_liqi.py
import os

import requests

Headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
}


def get_version():
    req = requests.get("https://game.maj-soul.com/1/version.json", headers=Headers)
    return req.json()


def get_prefix(version):
    req = requests.get(
        f"https://game.maj-soul.com/1/resversion{version}.json", headers=Headers
    )
    return req.json()["res"]["res/proto/liqi.json"]["prefix"]


def get_lqc_prefix(version):
    req = requests.get(
        f"https://game.maj-soul.com/1/resversion{version}.json", headers=Headers
    )
    return req.json()["res"]["res/config/lqc.lqbin"]["prefix"]


def get_liqi(prefix):
    req = requests.get(
        f"https://game.maj-soul.com/1/{prefix}/res/proto/liqi.json", headers=Headers
    )
    return req.text


def get_lqc(prefix):
    req = requests.get(
        f"https://game.maj-soul.com/1/{prefix}/res/config/lqc.lqbin", headers=Headers
    )
    return req.content


def get_code_js(code):
    req = requests.get(f"https://game.maj-soul.com/1/{code}", headers=Headers)
    return req.text


def main():
    version = get_version()
    prefix = get_prefix(version["version"])
    liqi = get_liqi(prefix)
    with open("liqi.json", "w") as f:
        f.write(liqi)
    env = os.getenv("GITHUB_ENV")
    with open(env, "a") as f:
        f.write(f"liqi-json={prefix}\n")


if __name__ == "__main__":
    main()
