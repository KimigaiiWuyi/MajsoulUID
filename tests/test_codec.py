import sys
import types
import struct
import importlib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "MajsoulUID"
PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"

# majs_notify/__init__.py 会拉起数据库与定时任务，这里只占位包、单独加载 codec
if f"{PKG}.majs_notify" not in sys.modules:
    stub = types.ModuleType(f"{PKG}.majs_notify")
    stub.__path__ = [str(PLUGIN_ROOT / "majs_notify")]
    sys.modules[f"{PKG}.majs_notify"] = stub

codec_mod = importlib.import_module(f"{PKG}.majs_notify.codec")
liblq = importlib.import_module(f"{PKG}.lib.lq")


@pytest.fixture(autouse=True)
def fresh_index(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(codec_mod, "REQUEST_INDEX", codec_mod.RequestIndex())


def make_codec():
    return codec_mod.MajsoulProtoCodec(codec_mod.RPC_MAP)


def unpack_request(buf: bytes):
    assert buf[0] == codec_mod.MajsoulProtoCodec.REQUEST
    index = buf[1] | (buf[2] << 8)
    return index, liblq.Wrapper().parse(buf[3:])


def response_bytes(index: int, payload) -> bytes:
    body = bytes(liblq.Wrapper(name="", data=bytes(payload)))
    return struct.pack("<BH", codec_mod.MajsoulProtoCodec.RESPONSE, index) + body


def test_rpc_map_covers_methods_used_by_plugin():
    for name in (
        ".lq.Route.heartbeat",
        ".lq.Route.requestConnection",
        ".lq.Lobby.login",
        ".lq.Lobby.oauth2Auth",
        ".lq.Lobby.oauth2Check",
        ".lq.Lobby.oauth2Login",
        ".lq.Lobby.loginBeat",
        ".lq.Lobby.loginSuccess",
        ".lq.Lobby.fetchInfo",
        ".lq.Lobby.fetchQueueInfo",
        ".lq.Lobby.fetchGameRecord",
        ".lq.Lobby.readGameRecord",
        ".lq.Lobby.fetchGameRecordsDetail",
        ".lq.Lobby.fetchServerTime",
        ".lq.Lobby.fetchGameLiveList",
        ".lq.Lobby.fetchMultiAccountBrief",
        ".lq.Lobby.handleFriendApply",
    ):
        rpc = codec_mod.RPC_MAP[name]
        codec = make_codec()
        assert codec.lookup_method(rpc.req) is not None
        assert codec.lookup_method(rpc.resp) is not None


def test_encode_record_requests():
    codec = make_codec()
    uuid = "261026-ddf54799-c3fa-48b0-99ad-532fefe01536"
    _, buf = codec.encode_request(".lq.Lobby.fetchGameRecordsDetail", liblq.ReqGameRecordsDetail(uuid_list=[uuid]))
    _, wrapper = unpack_request(buf)
    assert wrapper.name == ".lq.Lobby.fetchGameRecordsDetail"
    assert liblq.ReqGameRecordsDetail().parse(wrapper.data).uuid_list == [uuid]

    version = "WebGL_2022-0.16.289"
    _, buf = codec.encode_request(".lq.Lobby.readGameRecord", liblq.ReqGameRecord(client_version_string=version))
    read = liblq.ReqGameRecord().parse(unpack_request(buf)[1].data)
    assert (read.game_uuid, read.client_version_string) == ("", version)

    _, buf = codec.encode_request(".lq.Lobby.loginSuccess", liblq.ReqCommon())
    wrapper = unpack_request(buf)[1]
    assert (wrapper.name, wrapper.data) == (".lq.Lobby.loginSuccess", b"")


def test_encode_login_request_keeps_client_fields():
    codec = make_codec()
    index, buf = codec.encode_request(
        ".lq.Lobby.login",
        liblq.ReqLogin(
            account="someone@example.com",
            password="hashed",
            device=liblq.ClientDeviceInfo(platform="pc", is_browser=True, device_id="dev-1", screen_type=1),
            random_key="dev-1",
            client_version=liblq.ClientVersionInfo(resource="0.16.289", package="4.0.51"),
            currency_platforms=[1, 2, 5],
            client_version_string="WebGL_2022-0.16.289",
            tag="cn",
        ),
    )
    header_index, wrapper = unpack_request(buf)
    assert index == header_index == 1
    assert wrapper.name == ".lq.Lobby.login"

    req = liblq.ReqLogin().parse(wrapper.data)
    assert req.device.device_id == "dev-1"
    assert req.client_version.package == "4.0.51"
    assert req.currency_platforms == [1, 2, 5]
    assert req.client_version_string == "WebGL_2022-0.16.289"
    assert req.tag == "cn"


def test_request_index_wraps_like_client(monkeypatch: pytest.MonkeyPatch):
    # NetRoute._RequestToByte：i=(i+1)%60007，两字节小端
    counter = codec_mod.RequestIndex()
    counter._value = 60005
    monkeypatch.setattr(codec_mod, "REQUEST_INDEX", counter)
    codec = make_codec()
    indexes = [codec.encode_request(".lq.Lobby.loginSuccess", liblq.ReqCommon()) for _ in range(3)]
    assert [index for index, _ in indexes] == [60006, 0, 1]
    assert [unpack_request(buf)[0] for _, buf in indexes] == [60006, 0, 1]


def test_index_is_shared_across_codecs():
    # 官方计数器是模块级的：重连换了新连接也接着往下数
    first, _ = make_codec().encode_request(".lq.Lobby.loginSuccess", liblq.ReqCommon())
    second, _ = make_codec().encode_request(".lq.Lobby.loginSuccess", liblq.ReqCommon())
    assert second == first + 1


def test_decode_response_uses_inflight_type():
    codec = make_codec()
    index, _ = codec.encode_request(".lq.Route.requestConnection", liblq.ReqRequestConnection(type=1, route_id="r"))

    msg = codec.decode_message(response_bytes(index, liblq.ResRequestConnection(timestamp=123, result=1)))

    assert msg is not None
    assert msg.method_name == ".lq.Route.requestConnection"
    assert isinstance(msg.payload, liblq.ResRequestConnection)
    assert msg.payload.result == 1


def test_decode_notify_by_wrapper_name():
    codec = make_codec()
    notify = liblq.NotifyNewFriendApply(account_id=42)
    body = bytes(liblq.Wrapper(name=".lq.NotifyNewFriendApply", data=bytes(notify)))
    msg = codec.decode_message(bytes([codec.NOTIFY]) + body)

    assert msg is not None
    assert isinstance(msg.payload, liblq.NotifyNewFriendApply)
    assert msg.payload.account_id == 42


def test_unknown_notify_is_skipped():
    # 新版协议多了本地 lib/lq 没有的通知：跳过这条，不能让收消息循环退出
    body = bytes(liblq.Wrapper(name=".lq.NotifyFromTheFuture", data=b"\x08\x01"))
    assert make_codec().decode_message(bytes([codec_mod.MajsoulProtoCodec.NOTIFY]) + body) is None


def test_unknown_response_index_is_skipped():
    assert make_codec().decode_message(response_bytes(1234, liblq.ResCommon())) is None


def test_forgotten_request_ignores_late_response():
    codec = make_codec()
    index, _ = codec.encode_request(".lq.Lobby.loginSuccess", liblq.ReqCommon())
    codec.forget(index)
    assert codec.decode_message(response_bytes(index, liblq.ResCommon())) is None


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        make_codec().encode_request(".lq.Lobby.noSuchMethod", liblq.ReqCommon())


def test_wrong_request_type_raises():
    with pytest.raises(TypeError):
        make_codec().encode_request(".lq.Lobby.loginBeat", liblq.ReqCommon())
