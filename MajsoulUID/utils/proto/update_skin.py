"""刷新皮肤表 `lqc.json` 与好友排行榜用的皮肤头像。

- 皮肤表：取 `client_version.json` 对应标签下 MajsoulData 的 `item_definition/skin.json`
- 头像：只补本地缺的，从国服 Unity 资源包里解出 `bighead`，缩到 128px 存 WebP

先跑 `update_proto.py`，它写出本脚本要用的版本标签。需要 `UnityPy`。
"""

import io
import json
from typing import TypedDict, NotRequired
from pathlib import Path
from urllib.parse import urljoin

import httpx
import UnityPy
from PIL import Image

PROTO_DIR = Path(__file__).parent
CLIENT_VERSION_PATH = PROTO_DIR / "client_version.json"
SKIN_TABLE_PATH = PROTO_DIR / "lqc.json"
BIGHEAD_DIR = PROTO_DIR.parent / "texture2d" / "bighead"

SKIN_URL = "https://raw.githubusercontent.com/Avenshy/MajsoulData/{tag}/data/tables/item_definition/skin.json"
CLIENT_SETTINGS_URL = "https://game.maj-soul.com/assetbundles/clientBundleSettings/chs_t-release.json"
TEXTURE_PROFILES = ("ASTC", "DXT")
BIGHEAD_SIZE = (128, 128)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.62f2c1"


class ClientVersion(TypedDict):
    liqi_version: str


class SkinName(TypedDict, total=False):
    chs: str


class SkinRow(TypedDict):
    id: int
    name: SkinName
    character_id: int
    path: str | None


class SkinInfo(TypedDict):
    id: int
    name: str
    character_id: int
    path: str | None


class UrlEntry(TypedDict):
    url: str
    weight: NotRequired[int]
    Priority: NotRequired[int]


class Warehouse(TypedDict):
    urls: list[UrlEntry]
    warehouseSettingPath: str


class ClientSettings(TypedDict):
    warehouses: list[Warehouse]


class WarehouseSettings(TypedDict):
    urls: list[UrlEntry]
    bundlePath: str


def join_url(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def pick_url(entries: list[UrlEntry]) -> str:
    best = max(entries, key=lambda e: (e["Priority"] if "Priority" in e else 0, e["weight"] if "weight" in e else 0))
    return best["url"]


def build_skin_table(rows: list[SkinRow]) -> dict[str, SkinInfo]:
    table: dict[str, SkinInfo] = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        table[str(row["id"])] = {
            "id": row["id"],
            "name": row["name"]["chs"] if "chs" in row["name"] else "",
            "character_id": row["character_id"],
            "path": row["path"] or None,
        }
    return table


def bighead_asset_paths(skin: SkinInfo) -> list[str]:
    # 联动角色和 path 为空的皮肤，立绘放在以皮肤 ID 命名的目录下
    paths = [f"MyAssets/deco/character/{skin['id']}/bighead/bighead.png"]
    if skin["path"]:
        paths.insert(0, f"MyAssets/{skin['path']}/bighead/bighead.png")
    return paths


def resolve_profile_url(client: httpx.Client) -> str:
    settings: ClientSettings = client.get(CLIENT_SETTINGS_URL).raise_for_status().json()
    warehouse = settings["warehouses"][0]
    warehouse_url = join_url(pick_url(warehouse["urls"]), warehouse["warehouseSettingPath"])
    ws: WarehouseSettings = client.get(warehouse_url).raise_for_status().json()
    bundle_base = join_url(pick_url(ws["urls"]), ws["bundlePath"])
    for profile in TEXTURE_PROFILES:
        profile_url = join_url(bundle_base, profile) + "/"
        if client.get(join_url(profile_url, "bundle_hash.txt")).is_success:
            return profile_url
    raise RuntimeError(f"国服资源包没有可用的贴图格式: {TEXTURE_PROFILES}")


def load_asset_bundles(client: httpx.Client, profile_url: str) -> dict[str, str]:
    """返回 资源路径 → 所在资源包文件名。"""
    data = client.get(join_url(profile_url, "bundle_info_so.majset")).raise_for_status().content
    for obj in UnityPy.load(data).objects:
        if obj.type.name != "MonoBehaviour":
            continue
        tree = obj.read_typetree()
        if not isinstance(tree, dict) or "bundleInfos" not in tree or "assetInfos" not in tree:
            continue
        bundle_names = [str(b["name"]) for b in tree["bundleInfos"]]
        return {str(a["assetPath"]): bundle_names[int(a["ownerBundleIndex"])] for a in tree["assetInfos"]}
    raise RuntimeError("bundle_info_so.majset 里没有 BundleInfoSO")


def extract_bighead(bundle: bytes) -> Image.Image:
    for obj in UnityPy.load(bundle).objects:
        if obj.type.name != "Texture2D":
            continue
        image = obj.read().image
        if isinstance(image, Image.Image):
            return image.convert("RGBA").resize(BIGHEAD_SIZE, Image.Resampling.LANCZOS)
    raise RuntimeError("资源包里没有 Texture2D")


def update_bigheads(client: httpx.Client, table: dict[str, SkinInfo]) -> None:
    BIGHEAD_DIR.mkdir(parents=True, exist_ok=True)
    missing = [skin for skin in table.values() if not (BIGHEAD_DIR / f"{skin['id']}.webp").exists()]
    if not missing:
        print("皮肤头像已是最新")
        return

    profile_url = resolve_profile_url(client)
    assets = load_asset_bundles(client, profile_url)
    for skin in missing:
        found = [path for path in bighead_asset_paths(skin) if path in assets]
        if not found:
            print(f"跳过 {skin['id']} {skin['name']}: 资源包里没有 bighead")
            continue
        bundle = client.get(join_url(profile_url, assets[found[0]])).raise_for_status().content
        buf = io.BytesIO()
        extract_bighead(bundle).save(buf, "WEBP", quality=90, method=6)
        (BIGHEAD_DIR / f"{skin['id']}.webp").write_bytes(buf.getvalue())
        print(f"已导出 {skin['id']} {skin['name']}")


def main() -> None:
    version: ClientVersion = json.loads(CLIENT_VERSION_PATH.read_text(encoding="utf-8"))
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=60) as client:
        rows: list[SkinRow] = client.get(SKIN_URL.format(tag=version["liqi_version"])).raise_for_status().json()
        table = build_skin_table(rows)
        SKIN_TABLE_PATH.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"皮肤表 {version['liqi_version']}: {len(table)} 条")
        update_bigheads(client, table)


if __name__ == "__main__":
    main()
