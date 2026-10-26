import sys
import time
import types
import asyncio
import importlib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "MajsoulUID"
PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"

# majs_notify/__init__.py 会拉起数据库与定时任务，这里只占位包、单独加载 majsoul
if f"{PKG}.majs_notify" not in sys.modules:
    stub = types.ModuleType(f"{PKG}.majs_notify")
    stub.__path__ = [str(PLUGIN_ROOT / "majs_notify")]
    sys.modules[f"{PKG}.majs_notify"] = stub

majsoul = importlib.import_module(f"{PKG}.majs_notify.majsoul")
model = importlib.import_module(f"{PKG}.majs_notify.model")
constants = importlib.import_module(f"{PKG}.majs_notify.constants")
liblq = importlib.import_module(f"{PKG}.lib.lq")

FRIEND_ID = 17033013
OTHER_ID = 1194642
GAME_UUID = "261026-ddf54799-c3fa-48b0-99ad-532fefe01536"
SANMA_SOUTH_SILVER = 20
DETAIL = ".lq.Lobby.fetchGameRecordsDetail"


class FakeConfig:
    def __init__(self, **values: bool | int):
        self.values = values

    def get_config(self, key: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(data=self.values[key])


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> FakeConfig:
    cfg = FakeConfig(
        MajsPushOnlineState=True,
        MajsOnlineStateDelay=0.05,
        MajsPushGameStart=False,
        MajsIsPushActiveToMaster=False,
    )
    monkeypatch.setattr(majsoul, "MAJS_CONFIG", cfg)
    monkeypatch.setattr(majsoul, "GAME_RECORD_RETRY_DELAYS", (0, 0, 0))
    monkeypatch.setattr(majsoul, "RECORD_FETCH_INTERVAL", 0)
    return cfg


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    uuids: list[str] = []

    async def save_paipu(account_id: int, game_uuid: str, category: int, type_name: str) -> None:
        uuids.append(game_uuid)

    monkeypatch.setattr(majsoul, "save_paipu", save_paipu)
    return uuids


def playing(game_uuid: str = "") -> liblq.AccountPlayingGame:
    if not game_uuid:
        return liblq.AccountPlayingGame()
    return liblq.AccountPlayingGame(
        game_uuid=game_uuid, category=2, meta=liblq.GameMetaData(mode_id=SANMA_SOUTH_SILVER)
    )


def make_friend(account_id: int, nickname: str, online: bool, game_uuid: str):
    level = liblq.AccountLevel(id=20301, score=100)
    return liblq.Friend(
        base=liblq.PlayerBaseView(account_id=account_id, nickname=nickname, level=level, level3=level),
        state=liblq.AccountActiveState(account_id=account_id, is_online=online, playing=playing(game_uuid)),
    )


def make_conn(online: bool = True, game_uuid: str = "", records: list | None = None, region=None):
    route = types.SimpleNamespace(ssl=True, domain="example.invalid")
    conn = majsoul.MajsoulConnection(route, region, 0, None, "device")
    friends = {FRIEND_ID: "兰潇澜", OTHER_ID: "烟树历历"}
    conn.friends = {
        aid: majsoul.MajsoulFriend(make_friend(aid, nick, online, game_uuid)) for aid, nick in friends.items()
    }
    conn.sent = []
    conn.calls = []

    async def rpc_call(method_name: str, payload: dict):
        conn.calls.append(method_name)
        if method_name in (DETAIL, ".lq.Lobby.fetchGameRecord"):
            return records.pop(0)
        return liblq.ResCommon()

    async def send_msg_to_user(target_user: str, msg: str):
        conn.sent.append(msg)

    conn.rpc_call = rpc_call
    conn.send_msg_to_user = send_msg_to_user
    return conn


def state_change(online: bool, game_uuid: str = "", account_id: int = FRIEND_ID):
    state = liblq.AccountActiveState(account_id=account_id, is_online=online, playing=playing(game_uuid))
    payload = liblq.NotifyFriendStateChange(target_id=account_id, active_state=state)
    return model.MajsoulDecodedMessage(msg_type=1, req_index=0, method_name="", payload=payload)


def detail_error(code: int = 1203):
    return liblq.ResGameRecordsDetail(error=liblq.Error(code=code))


def detail_not_ready():
    return liblq.ResGameRecordsDetail()


def detail_ok():
    players = [
        liblq.GameEndResultPlayerItem(seat=1, part_point_1=52000, grading_score=45),
        liblq.GameEndResultPlayerItem(seat=0, part_point_1=31000, grading_score=0),
        liblq.GameEndResultPlayerItem(seat=2, part_point_1=22000, grading_score=-25),
    ]
    accounts = [
        liblq.RecordGameAccountInfo(account_id=FRIEND_ID, seat=1, level3=liblq.AccountLevel(id=20301, score=100)),
        liblq.RecordGameAccountInfo(account_id=OTHER_ID, seat=2, level3=liblq.AccountLevel(id=20301, score=100)),
    ]
    head = liblq.RecordGame(uuid=GAME_UUID, accounts=accounts, result=liblq.GameEndResult(players=players))
    return liblq.ResGameRecordsDetail(record_list=[head])


def paipu_url(account_id: int = FRIEND_ID) -> str:
    return f"{majsoul.PP_HOST}{GAME_UUID}_a{majsoul.encode_account_id(account_id)}"


async def settle(conn) -> None:
    await asyncio.sleep(0.1)
    assert conn._online_pending == {}


def test_online_flap_inside_window_pushes_nothing(config: FakeConfig):
    async def scenario():
        conn = make_conn(online=True)
        await conn.handle_FriendStateChange(state_change(False))
        await conn.handle_FriendStateChange(state_change(True))
        await conn.handle_FriendStateChange(state_change(False))
        await conn.handle_FriendStateChange(state_change(True))
        await settle(conn)
        return conn.sent

    assert asyncio.run(scenario()) == []


def test_online_change_pushes_once_after_it_settles(config: FakeConfig):
    async def scenario():
        conn = make_conn(online=True)
        await conn.handle_FriendStateChange(state_change(False))
        await conn.handle_FriendStateChange(state_change(True))
        await conn.handle_FriendStateChange(state_change(False))
        assert conn.sent == []
        await settle(conn)
        await conn.handle_FriendStateChange(state_change(True))
        await settle(conn)
        return conn.sent

    assert asyncio.run(scenario()) == ["兰潇澜 下线了", "兰潇澜 上线了"]


def test_online_push_can_be_turned_off(config: FakeConfig):
    config.values["MajsPushOnlineState"] = False

    async def scenario():
        conn = make_conn(online=True)
        await conn.handle_FriendStateChange(state_change(False))
        await settle(conn)
        return conn.sent

    assert asyncio.run(scenario()) == []


def test_game_start_saves_paipu_without_push(config: FakeConfig, saved: list[str]):
    conn = make_conn()
    asyncio.run(conn.handle_FriendStateChange(state_change(True, GAME_UUID)))
    assert conn.sent == []
    assert saved == [GAME_UUID]
    assert conn.friends[FRIEND_ID].playing.game_uuid == GAME_UUID


def test_game_start_push_has_no_game_id(config: FakeConfig, saved: list[str]):
    config.values["MajsPushGameStart"] = True
    conn = make_conn()
    asyncio.run(conn.handle_FriendStateChange(state_change(True, GAME_UUID)))
    assert conn.sent == ["兰潇澜 开始了在 银之间 · 三人南 的对局"]


def test_game_end_uses_record_detail_and_retries_until_ready(config: FakeConfig, saved: list[str]):
    conn = make_conn(game_uuid=GAME_UUID, records=[detail_error(), detail_not_ready(), detail_ok()])
    asyncio.run(conn.handle_FriendStateChange(state_change(True)))
    # 推送只查对局头部，不调会被限额的 fetchGameRecord
    assert conn.calls == [DETAIL, DETAIL, DETAIL]
    assert len(conn.sent) == 1
    lines = conn.sent[0].split("\n")
    assert lines[0] == "兰潇澜 结束了在 银之间 · 三人南 的对局"
    assert lines[1] == "排名:1 最终打点:52000 得点:45"
    assert lines[2].startswith("当前段位:")
    assert lines[3] == f"对局牌谱:{paipu_url()}"


def test_game_end_record_never_ready_sends_link(config: FakeConfig, saved: list[str]):
    conn = make_conn(game_uuid=GAME_UUID, records=[detail_not_ready() for _ in range(3)])
    asyncio.run(conn.handle_FriendStateChange(state_change(True)))
    assert conn.sent == [f"兰潇澜 结束了在 银之间 · 三人南 的对局\n获取对局结果失败\n对局牌谱:{paipu_url()}"]


def test_fetch_limit_gives_up_without_retrying(config: FakeConfig, saved: list[str]):
    conn = make_conn(game_uuid=GAME_UUID, records=[detail_error(540), detail_ok(), detail_ok()])
    asyncio.run(conn.handle_FriendStateChange(state_change(True)))
    assert conn.calls == [DETAIL]
    assert conn.sent == [f"兰潇澜 结束了在 银之间 · 三人南 的对局\n获取对局结果失败\n对局牌谱:{paipu_url()}"]


def test_friends_in_same_game_share_one_lookup(config: FakeConfig, saved: list[str]):
    async def scenario():
        conn = make_conn(game_uuid=GAME_UUID, records=[detail_not_ready(), detail_ok()])
        await asyncio.gather(
            conn.handle_FriendStateChange(state_change(True)),
            conn.handle_FriendStateChange(state_change(True, account_id=OTHER_ID)),
        )
        return conn

    conn = asyncio.run(scenario())
    assert conn.calls == [DETAIL, DETAIL]
    assert sorted(msg.split(" ")[0] for msg in conn.sent) == ["兰潇澜", "烟树历历"]
    assert any(f"对局牌谱:{paipu_url(OTHER_ID)}" in msg for msg in conn.sent)


def test_disconnect_mid_game_stays_silent(config: FakeConfig, saved: list[str]):
    # 中途掉线：playing 被清空、人离线，查不到结果不该推失败消息
    conn = make_conn(game_uuid=GAME_UUID, records=[detail_not_ready() for _ in range(3)])
    asyncio.run(conn.handle_FriendStateChange(state_change(False)))
    assert conn.sent == []


def test_state_updates_before_record_fetch_finishes(config: FakeConfig, saved: list[str]):
    # 查结果期间再来一条推送，不能再把同一局当成刚结束
    async def scenario():
        conn = make_conn(game_uuid=GAME_UUID, records=[detail_error(), detail_error(), detail_ok()])
        first = asyncio.create_task(conn.handle_FriendStateChange(state_change(True)))
        await asyncio.sleep(0)
        await conn.handle_FriendStateChange(state_change(True))
        await first
        return conn

    conn = asyncio.run(scenario())
    assert conn.calls == [DETAIL, DETAIL, DETAIL]
    assert len(conn.sent) == 1


def test_record_calls_keep_interval(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(majsoul, "RECORD_FETCH_INTERVAL", 0.05)

    async def scenario():
        conn = make_conn(records=[detail_ok() for _ in range(3)])
        stamps: list[float] = []

        async def one():
            await conn.record_call(DETAIL, {"uuid_list": [GAME_UUID]})
            stamps.append(time.monotonic())

        await asyncio.gather(one(), one(), one())
        return stamps

    stamps = asyncio.run(scenario())
    assert all(b - a >= 0.045 for a, b in zip(stamps, stamps[1:]))


def test_fetch_logs_returns_error_text_and_skips_read(config: FakeConfig):
    conn = make_conn(records=[liblq.ResGameRecord(error=liblq.Error(code=540))])
    assert asyncio.run(conn.fetchLogs("261026-not-cached")) == "超出牌谱拉取限制，请稍后重试"
    assert conn.calls == [".lq.Lobby.fetchGameRecord"]


def test_fetch_logs_downloads_data_url_after_read(config: FakeConfig, monkeypatch: pytest.MonkeyPatch):
    conn = make_conn(records=[liblq.ResGameRecord(data_url="/2610/abc")])
    urls: list[str] = []

    async def download_record_data(data_url: str) -> bytes:
        urls.append(data_url)
        return b""

    conn.download_record_data = download_record_data
    assert asyncio.run(conn.fetchLogs("261026-not-cached")) == "牌谱数据下载失败，请稍后重试"
    assert conn.calls == [".lq.Lobby.fetchGameRecord", ".lq.Lobby.readGameRecord"]
    assert urls == ["/2610/abc"]


def test_relative_data_url_gets_region_prefix(monkeypatch: pytest.MonkeyPatch):
    requested: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs: dict[str, str] | int):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args: object):
            return None

        async def get(self, url: str):
            requested.append(url)
            return types.SimpleNamespace(status_code=200, content=b"record")

    monkeypatch.setattr(majsoul.httpx2, "AsyncClient", FakeClient)
    conn = make_conn(region=constants.REGION_CN)
    assert asyncio.run(conn.download_record_data("/2610/abc")) == b"record"
    assert asyncio.run(conn.download_record_data("https://cdn.example/x")) == b"record"
    assert requested == [constants.REGION_CN.record_prefix_url + "/2610/abc", "https://cdn.example/x"]
    assert asyncio.run(make_conn(region=constants.REGION_JP).download_record_data("/2610/abc")) == b""


@pytest.mark.parametrize(
    ("nickname", "expected"),
    [("账号池", [".lq.Lobby.loginBeat", ".lq.Lobby.loginSuccess"]), ("", [".lq.Lobby.loginBeat"])],
)
def test_finish_login_reports_login_success(nickname: str, expected: list[str]):
    conn = make_conn()
    conn.nick_name = nickname
    asyncio.run(conn.finish_login())
    assert conn.calls == expected
