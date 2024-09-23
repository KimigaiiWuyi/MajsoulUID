import betterproto
from msgspec import Struct


class InflightRequest(Struct):
    method_name: str
    msg_obj: type[betterproto.Message]


class MajsoulVersionInfo(Struct):
    version: str
    force_version: str
    code: str


class ResItem(Struct):
    prefix: str


class MajsoulResInfo(Struct):
    res: dict[str, ResItem]


class TI(Struct):
    type: str
    id: int


class ReqRes(Struct):
    requestType: str
    responseType: str


class MajsoulLiqiItem(Struct):
    fields: dict[str, dict] | None = None
    methods: dict[str, ReqRes] | None = None


class MajsoulLiqiNested(Struct):
    nested: dict[str, MajsoulLiqiItem]


class MajsoulLiqiProto(Struct):
    nested: dict[str, MajsoulLiqiNested]


class MajsoulRegionUrl(Struct):
    ob_url: str
    url: str


class MajsoulConfigIp(Struct):
    contest_chat_url: str
    dhs_url: str
    name: str
    prefix_url: str
    region_urls: list[MajsoulRegionUrl]
    system_email_url: str


class MajsoulConfig(Struct):
    ip: list[MajsoulConfigIp]
    goods_sheleve_id: str
    emergency_url: str
    awsc_sdk_js: str
    nec_sdk_js: str
    tracker_url: str
    wapchat_url: str
    mycard_url: str
    homepage_url: str
    fb_oauth_url: str
    fb_sdk_js: str


class MajsoulServerList(Struct):
    servers: list[str]


class MajsoulDecodedMessage(Struct):
    msg_type: int
    req_index: int
    method_name: str
    payload: betterproto.Message


class MajsoulFriend(Struct):
    user_id: int
    nickname: str
    avatar: str
