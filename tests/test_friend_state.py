import sys
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
liblq = importlib.import_module(f"{PKG}.lib.lq")

FRIEND_ID = 17033013
GAME_UUID = "261026-ddf54799-c3fa-48b0-99ad-532fefe01536"
SANMA_SOUTH_SILVER = 20


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


def make_conn(online: bool = True, game_uuid: str = "", records: list | None = None):
    route = types.SimpleNamespace(ssl=True, domain="example.invalid")
    conn = majsoul.MajsoulConnection(route, None, 0, None, "device")
    level = liblq.AccountLevel(id=20301, score=100)
    friend = liblq.Friend(
        base=liblq.PlayerBaseView(account_id=FRIEND_ID, nickname="兰潇澜", level=level, level3=level),
        state=liblq.AccountActiveState(account_id=FRIEND_ID, is_online=online, playing=playing(game_uuid)),
    )
    conn.friends = {FRIEND_ID: majsoul.MajsoulFriend(friend)}
    conn.sent = []
    conn.rpc_calls = 0

    async def rpc_call(method_name: str, payload: dict):
        conn.rpc_calls += 1
        return records.pop(0)

    async def send_msg_to_user(target_user: str, msg: str):
        conn.sent.append(msg)

    conn.rpc_call = rpc_call
    conn.send_msg_to_user = send_msg_to_user
    return conn


def state_change(online: bool, game_uuid: str = ""):
    state = liblq.AccountActiveState(account_id=FRIEND_ID, is_online=online, playing=playing(game_uuid))
    payload = liblq.NotifyFriendStateChange(target_id=FRIEND_ID, active_state=state)
    return model.MajsoulDecodedMessage(msg_type=1, req_index=0, method_name="", payload=payload)


def record_error():
    return liblq.ResGameRecord(error=liblq.Error(code=1203))


def record_ok():
    players = [
        liblq.GameEndResultPlayerItem(seat=1, part_point_1=52000, grading_score=45),
        liblq.GameEndResultPlayerItem(seat=0, part_point_1=31000, grading_score=0),
        liblq.GameEndResultPlayerItem(seat=2, part_point_1=22000, grading_score=-25),
    ]
    account = liblq.RecordGameAccountInfo(account_id=FRIEND_ID, seat=1, level3=liblq.AccountLevel(id=20301, score=100))
    head = liblq.RecordGame(accounts=[account], result=liblq.GameEndResult(players=players))
    return liblq.ResGameRecord(head=head)


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


def test_game_end_retries_until_record_is_ready(config: FakeConfig, saved: list[str]):
    conn = make_conn(game_uuid=GAME_UUID, records=[record_error(), record_error(), record_ok()])
    asyncio.run(conn.handle_FriendStateChange(state_change(True)))
    assert conn.rpc_calls == 3
    assert len(conn.sent) == 1
    lines = conn.sent[0].split("\n")
    assert lines[0] == "兰潇澜 结束了在 银之间 · 三人南 的对局"
    assert lines[1] == "排名:1 最终打点:52000 得点:45"
    assert lines[2].startswith("当前段位:")
    assert lines[3] == f"对局牌谱:{majsoul.PP_HOST}{GAME_UUID}_a{majsoul.encode_account_id(FRIEND_ID)}"


def test_game_end_record_never_ready_sends_link(config: FakeConfig, saved: list[str]):
    conn = make_conn(game_uuid=GAME_UUID, records=[record_error() for _ in range(3)])
    asyncio.run(conn.handle_FriendStateChange(state_change(True)))
    url = f"{majsoul.PP_HOST}{GAME_UUID}_a{majsoul.encode_account_id(FRIEND_ID)}"
    assert conn.sent == [f"兰潇澜 结束了在 银之间 · 三人南 的对局\n获取牌谱失败\n对局牌谱:{url}"]


def test_disconnect_mid_game_stays_silent(config: FakeConfig, saved: list[str]):
    # 中途掉线：playing 被清空、人离线，牌谱查不到不该推「获取牌谱失败」
    conn = make_conn(game_uuid=GAME_UUID, records=[record_error() for _ in range(3)])
    asyncio.run(conn.handle_FriendStateChange(state_change(False)))
    assert conn.sent == []


def test_state_updates_before_record_fetch_finishes(config: FakeConfig, saved: list[str]):
    # 拉牌谱期间再来一条推送，不能再把同一局当成刚结束
    async def scenario():
        conn = make_conn(game_uuid=GAME_UUID, records=[record_error(), record_error(), record_ok()])
        first = asyncio.create_task(conn.handle_FriendStateChange(state_change(True)))
        await asyncio.sleep(0)
        await conn.handle_FriendStateChange(state_change(True))
        await first
        return conn

    conn = asyncio.run(scenario())
    assert conn.rpc_calls == 3
    assert len(conn.sent) == 1
