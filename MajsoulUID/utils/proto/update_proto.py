"""从 Avenshy/MajsoulData 最新 Release 刷新协议数据。

产物：
- `liqi.json`：RPC 全名 → 请求 / 响应消息名，运行时 codec 只认这份
- `client_version.json`：登录时上报的资源版本 / 客户端版本
- `--desc` 指定路径的 `liqi.desc`：交给 protoc 生成 `lib/lq`

雀魂换成 Unity 客户端后，官网 `resversion*.json` 里的 liqi.json 已停更。
"""

import os
import re
import json
import argparse
from typing import TypedDict
from pathlib import Path

import httpx2
from google.protobuf import descriptor_pb2

RELEASE_URL = "https://api.github.com/repos/Avenshy/MajsoulData/releases/latest"
DESC_ASSET = "liqi.desc"
PROTO_PACKAGE = "lq"
VERSION_RE = re.compile(r"^\d+(\.\d+)+$")

PROTO_DIR = Path(__file__).parent
RPC_MAP_PATH = PROTO_DIR / "liqi.json"
CLIENT_VERSION_PATH = PROTO_DIR / "client_version.json"


class ReleaseAsset(TypedDict):
    name: str
    browser_download_url: str


class Release(TypedDict):
    tag_name: str
    assets: list[ReleaseAsset]


class RpcTypes(TypedDict):
    req: str
    resp: str


class ClientVersion(TypedDict):
    liqi_version: str
    resource_version: str
    package_version: str


def github_headers() -> dict[str, str]:
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_release_tag(tag: str) -> ClientVersion:
    # 标签格式是 `<docs_version>-<product_version>`，见 MajsoulData 的 get_max_data.py
    resource_version, _, package_version = tag.partition("-")
    if not VERSION_RE.match(resource_version) or not VERSION_RE.match(package_version):
        raise ValueError(f"无法识别的 MajsoulData 版本标签: {tag}")
    return {
        "liqi_version": tag,
        "resource_version": resource_version,
        "package_version": package_version,
    }


def build_rpc_map(file_set: descriptor_pb2.FileDescriptorSet) -> dict[str, RpcTypes]:
    rpc_map: dict[str, RpcTypes] = {}
    for file in file_set.file:
        for service in file.service:
            for method in service.method:
                name = f".{file.package}.{service.name}.{method.name}"
                rpc_map[name] = {"req": method.input_type, "resp": method.output_type}
    return dict(sorted(rpc_map.items()))


def proto_files(file_set: descriptor_pb2.FileDescriptorSet) -> list[str]:
    return [file.name for file in file_set.file if file.package == PROTO_PACKAGE]


def write_json(path: Path, data: dict[str, RpcTypes] | ClientVersion) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desc", type=Path, default=Path(DESC_ASSET))
    args = parser.parse_args()
    desc_path: Path = args.desc

    with httpx2.Client(headers=github_headers(), follow_redirects=True, timeout=60) as client:
        resp = client.get(RELEASE_URL)
        resp.raise_for_status()
        release: Release = resp.json()
        tag = release["tag_name"]
        urls = [asset["browser_download_url"] for asset in release["assets"] if asset["name"] == DESC_ASSET]
        if not urls:
            raise RuntimeError(f"MajsoulData {tag} 缺少 {DESC_ASSET}")
        desc = client.get(urls[0])
        desc.raise_for_status()

    file_set = descriptor_pb2.FileDescriptorSet.FromString(desc.content)
    desc_path.write_bytes(desc.content)
    write_json(RPC_MAP_PATH, build_rpc_map(file_set))
    write_json(CLIENT_VERSION_PATH, parse_release_tag(tag))

    files = " ".join(proto_files(file_set))
    print(f"MajsoulData {tag}: {files}")
    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"proto_files={files}\n")


if __name__ == "__main__":
    main()
