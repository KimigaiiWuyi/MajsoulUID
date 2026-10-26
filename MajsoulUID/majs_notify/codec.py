import struct

import betterproto

from ..lib import lq as liblq
from .model import MajsoulRpc, InflightRequest, MajsoulDecodedMessage


class MajsoulProtoCodec:
    NOTIFY = 1
    REQUEST = 2
    RESPONSE = 3

    def __init__(self, rpc_map: dict[str, MajsoulRpc]):
        self._rpc_map = rpc_map
        self.index = 1
        self._inflight_requests: dict[int, InflightRequest] = {}

    def unwrap(self, wrapped: bytes):
        data = liblq.Wrapper().parse(wrapped)
        return data

    def wrap(self, name: str, data: bytes):
        wapper = liblq.Wrapper()
        wapper.name = name
        wapper.data = data
        return bytes(wapper)

    def lookup_method(self, path: str) -> type[betterproto.Message]:
        liqi_method = getattr(liblq, path.removeprefix(".lq."))
        return liqi_method

    def lookup_rpc(self, method_name: str) -> MajsoulRpc:
        if method_name not in self._rpc_map:
            raise ValueError(f"Unknown method {method_name}")
        return self._rpc_map[method_name]

    def decode_message(self, buf: bytes):
        type_byte = buf[0]

        if type_byte == self.NOTIFY:
            req_index = self.index
            msg = self.unwrap(buf[1:])
            method_name = msg.name
            _, lq, notify = method_name.split(".")
            msg_obj = self.lookup_method(notify)
        elif type_byte == self.REQUEST:
            req_index = buf[1] | (buf[2] << 8)
            msg = self.unwrap(buf[3:])
            method_name = msg.name
            msg_obj = self.lookup_method(self.lookup_rpc(method_name).req)
        elif type_byte == self.RESPONSE:
            req_index = buf[1] | (buf[2] << 8)
            msg = self.unwrap(buf[3:])
            inflight_req = self._inflight_requests.pop(req_index, None)
            if not inflight_req:
                raise ValueError(f"Unknown request {req_index}")
            msg_obj = inflight_req.msg_obj
            method_name = inflight_req.method_name
        else:
            raise ValueError(f"Invalid message type: {type_byte}")

        return MajsoulDecodedMessage(
            msg_type=type_byte,
            req_index=req_index,
            method_name=method_name,
            payload=msg_obj().parse(msg.data),
        )

    def encode_request(self, method_name: str, payload: dict):
        current_index = self.index
        self.index += 1

        rpc = self.lookup_rpc(method_name)
        requestType = self.lookup_method(rpc.req)
        responseType = self.lookup_method(rpc.resp)

        msg = requestType().from_dict(payload)
        msg = self.wrap(method_name, msg.SerializeToString())

        self._inflight_requests[current_index] = InflightRequest(
            method_name=method_name, msg_obj=responseType
        )

        data = (
            struct.pack(
                "<BBB", self.REQUEST, current_index & 0xFF, current_index >> 8
            )
            + msg
        )

        return data
