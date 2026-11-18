"""登录时上报的客户端版本（GameUtility.SetBIInfo / GameUtility.GetClientVersionString）。

平时用 `utils/proto/client_version.json`，由 Auto Liqi 每天从 MajsoulData 刷新。
雀魂更新后服务端回 151，官方提示重启游戏；重启时 GameMgr:InitConfigData 读热更新下来的
docs_version/version.json。插件照这个思路直接去国服 CDN 读它，换上新版本号（插件自己的逻辑）。
"""

import re
import time
import asyncio
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx2
import msgspec

from gsuid_core.pool import to_thread
from gsuid_core.logger import logger

from ..lib import lq as liblq
from .model import BundleUrl, DocsVersion, WarehouseSettings, ClientBundleSettings, MajsoulClientVersion
from .constants import (
    REGION_CN,
    USER_AGENT,
    CLIENT_PLATFORM,
    TEXTURE_PROFILES,
    DOCS_VERSION_ASSET,
    DOCS_VERSION_TEXT_ASSET,
    CLIENT_BUNDLE_SETTINGS_URL,
    CLIENT_VERSION_FETCH_TIMEOUT,
    CLIENT_VERSION_REFRESH_INTERVAL,
)
from ..utils.map import PROTO_PATH
from ..utils.unity_bundle import UnityBundleError, text_asset, bundle_of_asset

# WebGL 页面里 Unity 的 productVersion 就是 GameVersion.version
PRODUCT_VERSION_RE = re.compile(r"productVersion\s*:\s*[\"']([^\"']+)[\"']")
VERSION_RE = re.compile(r"\d+(\.\d+)+")


class OfficialVersionError(Exception):
    """官网页面或资源配置的内容对不上。"""


@dataclass(frozen=True)
class ClientVersion:
    # GameMgr.Inst.version，也就是 docs_version/version.json
    resource: str
    # GameVersion.version，WebGL 壳的版本
    package: str


def join_url(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def pick_url(entries: list[BundleUrl]) -> str:
    if not entries:
        raise OfficialVersionError("资源配置里没有下载地址")
    return max(entries, key=lambda entry: (entry.priority, entry.weight)).url


def product_version(page: str) -> str:
    found = PRODUCT_VERSION_RE.search(page)
    if found is None or not VERSION_RE.fullmatch(found.group(1)):
        raise OfficialVersionError("雀魂网页里没有 productVersion")
    return found.group(1)


def docs_version(bundle: bytes) -> str:
    version = msgspec.json.decode(text_asset(bundle, DOCS_VERSION_TEXT_ASSET), type=DocsVersion).version
    if not VERSION_RE.fullmatch(version):
        raise OfficialVersionError(f"资源版本号格式不对: {version}")
    return version


@to_thread
def find_docs_version_bundle(index: bytes) -> str:
    return bundle_of_asset(index, DOCS_VERSION_ASSET)


async def _profile_url(client: httpx2.AsyncClient, bundle_base: str) -> str:
    for profile in TEXTURE_PROFILES:
        profile_url = join_url(bundle_base, profile) + "/"
        if (await client.get(join_url(profile_url, "bundle_hash.txt"))).is_success:
            return profile_url
    raise OfficialVersionError(f"国服资源包没有可用的贴图格式: {TEXTURE_PROFILES}")


async def fetch_official_version() -> ClientVersion:
    """照 WebGL 客户端热更新的路径取当前版本：网页 → 资源配置 → 资源索引 → docs_version。"""
    headers = {"User-Agent": USER_AGENT, "Referer": REGION_CN.web_url}
    async with httpx2.AsyncClient(
        headers=headers, follow_redirects=True, timeout=CLIENT_VERSION_FETCH_TIMEOUT
    ) as client:
        page = (await client.get(REGION_CN.web_url)).raise_for_status()
        package = product_version(page.text)
        settings_resp = (await client.get(CLIENT_BUNDLE_SETTINGS_URL)).raise_for_status()
        settings = msgspec.json.decode(settings_resp.content, type=ClientBundleSettings)
        if not settings.warehouses:
            raise OfficialVersionError("资源配置里没有仓库")
        warehouse = settings.warehouses[0]
        warehouse_resp = (
            await client.get(join_url(pick_url(warehouse.urls), warehouse.setting_path))
        ).raise_for_status()
        warehouse_settings = msgspec.json.decode(warehouse_resp.content, type=WarehouseSettings)
        profile_url = await _profile_url(
            client, join_url(pick_url(warehouse_settings.urls), warehouse_settings.bundle_path)
        )
        index = (await client.get(join_url(profile_url, "bundle_info_so.majset"))).raise_for_status().content
        name = await find_docs_version_bundle(index)
        bundle = (await client.get(join_url(profile_url, name))).raise_for_status().content
    return ClientVersion(resource=docs_version(bundle), package=package)


class ClientVersionState:
    def __init__(self, bundled: MajsoulClientVersion) -> None:
        self.current = ClientVersion(resource=bundled.resource_version, package=bundled.package_version)
        self._lock = asyncio.Lock()
        self._checked_at: float | None = None

    @property
    def version_string(self) -> str:
        # GameUtility.GetClientVersionString：平台 .. '_2022-' .. GameMgr.Inst.version
        return f"{CLIENT_PLATFORM}_2022-{self.current.resource}"

    def info(self) -> liblq.ClientVersionInfo:
        return liblq.ClientVersionInfo(resource=self.current.resource, package=self.current.package)

    async def refresh(self, outdated: ClientVersion) -> bool:
        """`outdated` 被服务端判成版本过旧（151）；换上了别的版本号就返回 True。"""
        async with self._lock:
            if self.current != outdated:
                # 别的号已经刷新过了，直接用新的
                return True
            now = time.monotonic()
            if self._checked_at is not None and now - self._checked_at < CLIENT_VERSION_REFRESH_INTERVAL:
                return False
            self._checked_at = now
            try:
                latest = await fetch_official_version()
            except (httpx2.HTTPError, msgspec.DecodeError, UnityBundleError, OfficialVersionError) as e:
                logger.warning(f"[majs] 从雀魂官网获取最新版本号失败: {e!r}")
                return False
            if latest == outdated:
                logger.warning(f"[majs] 雀魂官网上也是 {latest.resource} / {latest.package}，151 不是版本号的问题")
                return False
            logger.info(
                f"[majs] 客户端版本 {outdated.resource} / {outdated.package} → {latest.resource} / {latest.package}"
            )
            self.current = latest
            return True


CLIENT_VERSION = ClientVersionState(
    msgspec.json.decode((PROTO_PATH / "client_version.json").read_bytes(), type=MajsoulClientVersion)
)
