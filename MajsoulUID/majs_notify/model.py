from typing import List

import betterproto
from msgspec import Struct, field

from ..lib.lq import RecordGame


class InflightRequest(Struct):
    method_name: str
    msg_obj: type[betterproto.Message]


class MajsoulRpc(Struct):
    req: str
    resp: str


class MajsoulClientVersion(Struct):
    liqi_version: str
    resource_version: str
    package_version: str


class MajsoulRoute(Struct):
    id: str
    domain: str
    ssl: bool
    # 网关给的线路状态：idle / normal / busy / ...；level 越高越优先（Entrance 选主线路）
    state: str = "idle"
    level: int = 0
    order: int = 1000
    name: str = ""


class MajsoulMaintenanceInfo(Struct):
    lang: str = ""
    content: str = ""


class MajsoulRoutesData(Struct):
    routes: List[MajsoulRoute] = []
    maintenance: List[MajsoulMaintenanceInfo] = []


class MajsoulRoutesResp(Struct):
    data: MajsoulRoutesData


class BundleUrl(Struct):
    url: str
    weight: int = 0
    priority: int = field(default=0, name="Priority")


class BundleWarehouse(Struct):
    urls: List[BundleUrl]
    setting_path: str = field(name="warehouseSettingPath")


class ClientBundleSettings(Struct):
    """国服 clientBundleSettings/chs_t-release.json：热更新资源仓库的入口。"""

    warehouses: List[BundleWarehouse]


class WarehouseSettings(Struct):
    urls: List[BundleUrl]
    bundle_path: str = field(name="bundlePath")


class DocsVersion(Struct):
    """资源包里的 docs_version/version.json，GameMgr:InitConfigData 读它当资源版本。"""

    version: str


class YostarPassportResp(Struct):
    """passport.mahjongsoul.com 各接口的回包，result 为 0 才算成功。"""

    result: int
    uid: int | str = ""
    token: str = ""
    accessToken: str = ""


class MajsoulDecodedMessage(Struct):
    msg_type: int
    req_index: int
    method_name: str
    payload: betterproto.Message


class MajsoulFriend(Struct):
    user_id: int
    nickname: str
    avatar: str


class MjsLogItem(Struct):
    name: str
    data: betterproto.Message


class MjsLog(Struct):
    head: RecordGame
    data: List[MjsLogItem]
