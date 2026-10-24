from typing import List, TypedDict

import betterproto
from msgspec import Struct

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


class MajsoulMaintenanceInfo(Struct):
    lang: str = ""
    content: str = ""


class MajsoulRoutesData(Struct):
    routes: List[MajsoulRoute] = []
    maintenance: List[MajsoulMaintenanceInfo] = []


class MajsoulRoutesResp(Struct):
    data: MajsoulRoutesData


class MajsoulDeviceInfo(TypedDict):
    platform: str
    hardware: str
    os: str
    os_version: str
    is_browser: bool
    sale_platform: str
    user_agent: str
    screen_width: int
    screen_height: int
    screen_type: int
    device_id: str


class MajsoulClientVersionInfo(TypedDict):
    resource: str
    package: str


class MajsoulLoginFields(TypedDict):
    """login / oauth2Login 共有的客户端信息字段。"""

    device: MajsoulDeviceInfo
    random_key: str
    client_version: MajsoulClientVersionInfo
    currency_platforms: List[int]
    client_version_string: str
    tag: str


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
