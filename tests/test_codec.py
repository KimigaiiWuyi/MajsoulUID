import sys
import types
import struct
import importlib
from pathlib import Path

import pytest
import msgspec

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "MajsoulUID"
PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"

# majs_notify/__init__.py 会拉起数据库与定时任务，这里只占位包、单独加载 codec
if f"{PKG}.majs_notify" not in sys.modules:
    stub = types.ModuleType(f"{PKG}.majs_notify")
    stub.__path__ = [str(PLUGIN_ROOT / "majs_notify")]
    sys.modules[f"{PKG}.majs_notify"] = stub

codec_mod = importlib.import_module(f"{PKG}.majs_notify.codec")
model_mod = importlib.import_module(f"{PKG}.majs_notify.model")
liblq = importlib.import_module(f"{PKG}.lib.lq")

RPC_MAP = msgspec.json.decode(
    (PLUGIN_ROOT / "utils" / "proto" / "liqi.json").read_bytes(),
    type=dict[str, model_mod.MajsoulRpc],
)


def make_codec():
    return codec_mod.MajsoulProtoCodec(RPC_MAP)


def unpack_request(buf: bytes):
    assert buf[0] == codec_mod.MajsoulProtoCodec.REQUEST
    index = buf[1] | (buf[2] << 8)
    return index, liblq.Wrapper().parse(buf[3:])


def test_rpc_map_covers_methods_used_by_plugin():
    for name in (
        ".lq.Route.heartbeat",
        ".lq.Route.requestConnection",
        ".lq.Lobby.login",
        ".lq.Lobby.oauth2Auth",
        ".lq.Lobby.oauth2Check",
        ".lq.Lobby.oauth2Login",
        ".lq.Lobby.loginBeat",
        ".lq.Lobby.fetchInfo",
        ".lq.Lobby.fetchGameRecord",
        ".lq.Lobby.fetchServerTime",
        ".lq.Lobby.fetchGameLiveList",
        ".lq.Lobby.fetchMultiAccountBrief",
        ".lq.Lobby.handleFriendApply",
    ):
        rpc = RPC_MAP[name]
        codec = make_codec()
        assert codec.lookup_method(rpc.req) is not None
        assert codec.lookup_method(rpc.resp) is not None


def test_encode_login_request_keeps_client_fields():
    codec = make_codec()
    buf = codec.encode_request(
        ".lq.Lobby.login",
        {
            "account": "someone@example.com",
            "password": "hashed",
            "device": {"platform": "pc", "is_browser": True, "device_id": "dev-1", "screen_type": 1},
            "random_key": "dev-1",
            "client_version": {"resource": "0.16.289", "package": "4.0.51"},
            "currency_platforms": [1, 2, 5],
            "client_version_string": "WebGL_2022-0.16.289",
            "tag": "cn",
        },
    )
    index, wrapper = unpack_request(buf)
    assert index == 1
    assert wrapper.name == ".lq.Lobby.login"

    req = liblq.ReqLogin().parse(wrapper.data)
    assert req.device.device_id == "dev-1"
    assert req.client_version.package == "4.0.51"
    assert req.currency_platforms == [1, 2, 5]
    assert req.client_version_string == "WebGL_2022-0.16.289"
    assert req.tag == "cn"


def test_decode_response_uses_inflight_type():
    codec = make_codec()
    index, _ = unpack_request(codec.encode_request(".lq.Route.requestConnection", {"type": 1, "route_id": "r"}))

    res = liblq.ResRequestConnection(timestamp=123, result=1)
    body = bytes(liblq.Wrapper(name="", data=bytes(res)))
    msg = codec.decode_message(struct.pack("<BBB", codec.RESPONSE, index & 0xFF, index >> 8) + body)

    assert msg.method_name == ".lq.Route.requestConnection"
    assert isinstance(msg.payload, liblq.ResRequestConnection)
    assert msg.payload.result == 1


def test_decode_notify_by_wrapper_name():
    codec = make_codec()
    notify = liblq.NotifyNewFriendApply(account_id=42)
    body = bytes(liblq.Wrapper(name=".lq.NotifyNewFriendApply", data=bytes(notify)))
    msg = codec.decode_message(bytes([codec.NOTIFY]) + body)

    assert isinstance(msg.payload, liblq.NotifyNewFriendApply)
    assert msg.payload.account_id == 42


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        make_codec().encode_request(".lq.Lobby.noSuchMethod", {})
