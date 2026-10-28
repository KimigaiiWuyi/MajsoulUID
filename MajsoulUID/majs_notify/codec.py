import struct

import msgspec
import betterproto

from gsuid_core.logger import logger

from ..lib import lq as liblq
from .model import MajsoulRpc, InflightRequest, MajsoulClientVersion, MajsoulDecodedMessage
from .constants import REQUEST_INDEX_MOD
from ..utils.map import PROTO_PATH

# 两份文件都由 utils/proto/update_proto.py 从 MajsoulData 生成
RPC_MAP = msgspec.json.decode((PROTO_PATH / "liqi.json").read_bytes(), type=dict[str, MajsoulRpc])
CLIENT_VERSION = msgspec.json.decode((PROTO_PATH / "client_version.json").read_bytes(), type=MajsoulClientVersion)

MESSAGE_TYPES: dict[str, type[betterproto.Message]] = {
    name: obj for name, obj in vars(liblq).items() if isinstance(obj, type) and issubclass(obj, betterproto.Message)
}


def message_type(name: str) -> type[betterproto.Message] | None:
    short = name.removeprefix(".lq.")
    return MESSAGE_TYPES[short] if short in MESSAGE_TYPES else None


class RequestIndex:
    # NetRoute.lua 的 local i 是模块级的，所有路由共用、重连也不清零
    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        self._value = (self._value + 1) % REQUEST_INDEX_MOD
        return self._value


REQUEST_INDEX = RequestIndex()


class MajsoulProtoCodec:
    NOTIFY = 1
    REQUEST = 2
    RESPONSE = 3

    def __init__(self, rpc_map: dict[str, MajsoulRpc]):
        self._rpc_map = rpc_map
        self._inflight_requests: dict[int, InflightRequest] = {}

    def lookup_rpc(self, method_name: str) -> MajsoulRpc:
        if method_name not in self._rpc_map:
            raise ValueError(f"Unknown method {method_name}")
        return self._rpc_map[method_name]

    def lookup_method(self, path: str) -> type[betterproto.Message]:
        msg_type = message_type(path)
        if msg_type is None:
            raise ValueError(f"liqi.json 里的 {path} 在 lib/lq 里没有生成")
        return msg_type

    def encode_request(self, method_name: str, req: betterproto.Message) -> tuple[int, bytes]:
        rpc = self.lookup_rpc(method_name)
        req_type = self.lookup_method(rpc.req)
        if not isinstance(req, req_type):
            raise TypeError(f"{method_name} 的请求要 {req_type.__name__}，传进来的是 {type(req).__name__}")
        index = REQUEST_INDEX.next()
        self._inflight_requests[index] = InflightRequest(method_name=method_name, msg_obj=self.lookup_method(rpc.resp))
        wrapper = liblq.Wrapper(name=method_name, data=bytes(req))
        return index, struct.pack("<BH", self.REQUEST, index) + bytes(wrapper)

    def forget(self, index: int) -> None:
        self._inflight_requests.pop(index, None)

    def decode_message(self, buf: bytes) -> MajsoulDecodedMessage | None:
        """解不出来返回 None，和 NetRoute:_ParseMessageProto 一样跳过这条，不断开连接。"""
        if not buf:
            return None
        kind = buf[0]
        if kind == self.NOTIFY:
            wrapper = liblq.Wrapper().parse(buf[1:])
            notify_type = message_type(wrapper.name)
            if notify_type is None:
                logger.info(f"[majs] 本地 lib/lq 没有通知类型 {wrapper.name}，跳过")
                return None
            payload = notify_type().parse(wrapper.data)
            return MajsoulDecodedMessage(msg_type=kind, req_index=0, method_name=wrapper.name, payload=payload)
        if kind == self.RESPONSE and len(buf) >= 3:
            index = buf[1] | (buf[2] << 8)
            inflight = self._inflight_requests.pop(index, None)
            if inflight is None:
                logger.warning(f"[majs] 收到不存在的 requestID: {index}")
                return None
            wrapper = liblq.Wrapper().parse(buf[3:])
            payload = inflight.msg_obj().parse(wrapper.data)
            return MajsoulDecodedMessage(
                msg_type=kind, req_index=index, method_name=inflight.method_name, payload=payload
            )
        logger.warning(f"[majs] 收到无法识别的消息类型: {kind}")
        return None
