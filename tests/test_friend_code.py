import sys
import types
import asyncio
import importlib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "MajsoulUID"
PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"

remote = importlib.import_module(f"{PKG}.utils.api.remote")


@pytest.mark.parametrize(
    ("account_id", "friend_code"),
    [
        # 游戏内「我的ID」截图与牌谱屋 UID 对照
        (12536235, 34273564),
        # 账号池登录时写入 MajsUser.friend_code 的值
        (15340221, 75005974),
    ],
)
def test_friend_code_matches_in_game_id(account_id, friend_code):
    assert remote.encode_account_id2(account_id) == friend_code
    assert remote.decode_friend_code(friend_code) == account_id


@pytest.mark.parametrize("account_id", [1, 218174, 1194642, 17033013, 67108863, 120000000, 0xFFFFFFFF])
def test_decode_friend_code_round_trip(account_id):
    assert remote.decode_friend_code(remote.encode_account_id2(account_id)) == account_id


class FakeConn:
    def __init__(self, accounts: dict[int, str], closed: bool = False, friends: dict[int, str] | None = None):
        self.accounts = accounts
        self.closed = closed
        self.friends = {aid: types.SimpleNamespace(nickname=nick) for aid, nick in (friends or {}).items()}
        self.lookups: list[int] = []

    def is_closed(self) -> bool:
        return self.closed

    async def fetchAccountNickname(self, account_id: int) -> str | None:
        self.lookups.append(account_id)
        return self.accounts.get(account_id)


class FakeManager:
    def __init__(self, conn: FakeConn | None):
        self.conn = conn

    def get_conn(self) -> FakeConn | None:
        return self.conn

    def get_all_conn(self) -> list[FakeConn]:
        return [self.conn] if self.conn else []


def load_resolver(monkeypatch: pytest.MonkeyPatch, conn: FakeConn | None):
    # majs_user / majs_notify 的 __init__ 会注册命令、连数据库，这里只占位包
    for name in ("majs_user", "majs_notify"):
        if f"{PKG}.{name}" not in sys.modules:
            stub = types.ModuleType(f"{PKG}.{name}")
            stub.__path__ = [str(PLUGIN_ROOT / name)]
            monkeypatch.setitem(sys.modules, f"{PKG}.{name}", stub)
    majsoul = types.ModuleType(f"{PKG}.majs_notify.majsoul")
    majsoul.MajsoulConnection = FakeConn
    majsoul.manager = FakeManager(conn)
    monkeypatch.setitem(sys.modules, f"{PKG}.majs_notify.majsoul", majsoul)
    monkeypatch.delitem(sys.modules, f"{PKG}.majs_user.resolve_uid", raising=False)
    return importlib.import_module(f"{PKG}.majs_user.resolve_uid")


UID = 12536235
CODE = 34273564


def test_uid_is_kept_and_shows_nickname(monkeypatch):
    mod = load_resolver(monkeypatch, FakeConn({UID: "浅碎丶时空"}))
    target = asyncio.run(mod.resolve_bind_uid(str(UID)))
    assert (target.uid, target.nickname, target.note) == (str(UID), "浅碎丶时空", "")


def test_friend_code_is_converted_to_uid(monkeypatch):
    mod = load_resolver(monkeypatch, FakeConn({UID: "浅碎丶时空"}))
    target = asyncio.run(mod.resolve_bind_uid(str(CODE)))
    assert (target.uid, target.nickname) == (str(UID), "浅碎丶时空")
    assert "好友码" in target.note


def test_both_readings_valid_keeps_input_and_explains(monkeypatch):
    mod = load_resolver(monkeypatch, FakeConn({UID: "浅碎丶时空", CODE: "另一个人"}))
    target = asyncio.run(mod.resolve_bind_uid(str(CODE)))
    assert (target.uid, target.nickname) == (str(CODE), "另一个人")
    assert "浅碎丶时空" in target.note and f"雀魂绑定uid{UID}" in target.note


def test_unknown_number_is_bound_with_hint(monkeypatch):
    mod = load_resolver(monkeypatch, FakeConn({}))
    target = asyncio.run(mod.resolve_bind_uid("99999999"))
    assert (target.uid, target.nickname) == ("99999999", None)
    assert "雀魂搜索" in target.note


@pytest.mark.parametrize("conn", [None, FakeConn({UID: "浅碎丶时空"}, closed=True)])
def test_no_pool_falls_back_to_tip(monkeypatch, conn):
    mod = load_resolver(monkeypatch, conn)
    target = asyncio.run(mod.resolve_bind_uid(str(CODE)))
    assert target == mod.BindTarget(str(CODE), None, mod.FRIEND_CODE_TIP)


def test_uid_that_is_bot_friend_skips_ambiguity_note(monkeypatch):
    # 实际发生过：12536235 当好友码看也对应一个真号，但它本身就是机器人好友，不该再提示
    conn = FakeConn({UID: "浅碎丶时空", 17162248: "别人"}, friends={UID: "浅碎丶时空"})
    mod = load_resolver(monkeypatch, conn)
    target = asyncio.run(mod.resolve_bind_uid(str(UID)))
    assert (target.uid, target.nickname, target.note) == (str(UID), "浅碎丶时空", "")
    assert conn.lookups == []


def test_friend_code_of_bot_friend_is_converted(monkeypatch):
    conn = FakeConn({UID: "浅碎丶时空", CODE: "另一个人"}, friends={UID: "浅碎丶时空"})
    mod = load_resolver(monkeypatch, conn)
    target = asyncio.run(mod.resolve_bind_uid(str(CODE)))
    assert (target.uid, target.nickname) == (str(UID), "浅碎丶时空")
    assert "好友码" in target.note
