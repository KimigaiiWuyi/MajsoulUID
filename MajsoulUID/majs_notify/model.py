from typing import List

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
