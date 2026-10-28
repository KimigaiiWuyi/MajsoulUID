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

ConnState = majsoul.ConnState
# 好友总览截图里的四个人
FRIENDS = {1194642: "烟树历历", 17033013: "兰潇澜", 12536235: "浅碎丶时空", 218174: "雨滴答滴答"}


def make_friend(account_id: int, nickname: str):
    level = liblq.AccountLevel(id=10301)
    return liblq.Friend(base=liblq.PlayerBaseView(account_id=account_id, nickname=nickname, level=level, level3=level))


def make_conn(responses: list | None = None, username: str = "u"):
    credential = majsoul.MajsoulCredential(login_type=0, username=username, password="p")
    conn = majsoul.MajsoulConnection(credential, "device")
    conn.metas = []

    async def rpc_call(method_name: str, req, resp_type):
        return responses.pop(0)

    async def send_meta(msg: str):
        conn.metas.append(msg)

    conn.rpc_call = rpc_call
    conn.send_meta = send_meta
    return conn


def fetch_info_resp(friends: dict[int, str], applies: list[int], error_code: int = 0):
    return liblq.ResFetchInfo(
        error=liblq.Error(code=error_code),
        friend_list=liblq.ResFriendList(friends=[make_friend(aid, nick) for aid, nick in friends.items()]),
        friend_apply_list=liblq.ResFriendApplyList(
            applies=[liblq.ResFriendApplyListFriendApply(account_id=aid) for aid in applies]
        ),
    )


def friend_change(account_id: int, change_type: int, nickname: str = ""):
    payload = liblq.NotifyFriendChange(
        account_id=account_id, type=change_type, friend=make_friend(account_id, nickname)
    )
    return model.MajsoulDecodedMessage(msg_type=1, req_index=0, method_name="", payload=payload)


def test_fetch_info_repeated_keeps_one_entry_per_friend():
    # 登录一次 + 雀魂订阅四次 = 五次 fetchInfo，之前好友总览会出现五遍
    conn = make_conn([fetch_info_resp(FRIENDS, [42, 42]) for _ in range(5)])
    for _ in range(5):
        asyncio.run(conn.fetchInfo())
    assert {aid: f.nickname for aid, f in conn.friends.items()} == FRIENDS
    assert conn.friend_apply_list == [42]


def test_fetch_info_drops_friends_removed_on_server():
    conn = make_conn([fetch_info_resp(FRIENDS, []), fetch_info_resp({218174: "雨滴答滴答"}, [])])
    asyncio.run(conn.fetchInfo())
    asyncio.run(conn.fetchInfo())
    assert list(conn.friends) == [218174]


def test_fetch_info_error_keeps_old_list():
    conn = make_conn([fetch_info_resp(FRIENDS, [7]), fetch_info_resp({}, [], error_code=1)])
    asyncio.run(conn.fetchInfo())
    asyncio.run(conn.fetchInfo())
    assert len(conn.friends) == 4
    assert conn.friend_apply_list == [7]


def test_get_conn_skips_offline_connection():
    manager = majsoul.MajsoulManager()
    kicked, alive = make_conn(), make_conn()
    kicked.state = ConnState.KICKED
    alive.state = ConnState.ONLINE
    manager.conn = [kicked, alive]
    assert manager.get_conn() is alive
    alive.state = ConnState.RECONNECTING
    assert manager.get_conn() is None


def fake_pool(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, str]) -> list[str]:
    """账号池里放 outcomes 里的号；值为 "ok" 的登录成功，其余当作失败文案。"""
    logins: list[str] = []

    async def get_all_user():
        return [types.SimpleNamespace(username=name) for name in outcomes]

    def connection_from_user(user: types.SimpleNamespace):
        conn = make_conn([fetch_info_resp(FRIENDS, [])], username=user.username)

        async def login():
            await asyncio.sleep(0.01)
            logins.append(user.username)
            if outcomes[user.username] != "ok":
                conn.state, conn.last_error = ConnState.FAILED, outcomes[user.username]
                raise majsoul.MajsoulLoginError(outcomes[user.username])
            conn.state = ConnState.ONLINE
            await conn.fetchInfo()

        conn.login = login
        return conn

    monkeypatch.setattr(majsoul, "MajsUser", types.SimpleNamespace(get_all_user=get_all_user))
    monkeypatch.setattr(majsoul, "connection_from_user", connection_from_user)
    return logins


def test_concurrent_start_logs_in_once(monkeypatch: pytest.MonkeyPatch):
    # 定时任务和指令同时 start()，同一个号登两次，先登的那条就会被顶掉
    logins = fake_pool(monkeypatch, {"u": "ok"})
    manager = majsoul.MajsoulManager()

    async def start_twice():
        return await asyncio.gather(manager.start(), manager.start())

    first, second = asyncio.run(start_twice())
    assert logins == ["u"]
    assert first == second
    assert len(manager.conn[0].friends) == len(FRIENDS)


def test_start_keeps_going_when_one_account_fails(monkeypatch: pytest.MonkeyPatch):
    # 之前第一个号失败就直接 return，后面的号永远登不上
    logins = fake_pool(monkeypatch, {"bad": "账号被封禁", "good": "ok"})
    manager = majsoul.MajsoulManager()
    lines = asyncio.run(manager.start())
    assert logins == ["bad", "good"]
    assert lines[0].startswith("❌") and "账号被封禁" in lines[0]
    assert lines[1].startswith("✅")
    assert manager.get_conn() is manager.conn[1]


def test_start_does_not_relogin_online_accounts(monkeypatch: pytest.MonkeyPatch):
    logins = fake_pool(monkeypatch, {"u": "ok"})
    manager = majsoul.MajsoulManager()
    asyncio.run(manager.start())
    asyncio.run(manager.start())
    assert logins == ["u"]


def test_add_account_closes_same_account_before_login(monkeypatch: pytest.MonkeyPatch):
    # 旧会话不先关掉，新登录会把它顶下线，它再自动重登又把新的顶掉
    events: list[str] = []
    old = make_conn(username="same")
    old.state = ConnState.ONLINE

    async def close_old():
        events.append("close old")
        old.state = ConnState.OFFLINE

    old.close = close_old

    async def login(self):
        events.append(f"login {self.credential.username}")
        self.account_id = 42
        self.state = ConnState.ONLINE

    monkeypatch.setattr(majsoul.MajsoulConnection, "login", login)
    manager = majsoul.MajsoulManager()
    manager.conn = [old]
    manager._loaded = True
    credential = majsoul.MajsoulCredential(login_type=0, username="same", password="new")
    conn = asyncio.run(manager.add_account(credential))
    assert events == ["close old", "login same"]
    assert manager.conn == [conn]


def test_failed_readd_puts_old_session_back(monkeypatch: pytest.MonkeyPatch):
    # 比如重新添加时密码输错：旧会话已经关了，得放回账号池重新登录，不能让这个号消失
    events: list[str] = []
    old = make_conn(username="same")
    old.state = ConnState.ONLINE

    async def close_old():
        events.append("close old")
        old.state = ConnState.OFFLINE

    async def login_old():
        events.append("login old")
        old.state = ConnState.ONLINE

    old.close = close_old
    old.login = login_old

    async def login_new(self):
        events.append("login new")
        raise majsoul.MajsoulLoginError("账密登录失败（错误码 1002）")

    monkeypatch.setattr(majsoul.MajsoulConnection, "login", login_new)
    manager = majsoul.MajsoulManager()
    manager.conn = [old]
    manager._loaded = True
    credential = majsoul.MajsoulCredential(login_type=0, username="same", password="wrong")
    with pytest.raises(majsoul.MajsoulLoginError):
        asyncio.run(manager.add_account(credential))
    assert events == ["close old", "login new", "login old"]
    assert manager.conn == [old]
    assert manager.get_conn() is old


def test_friend_change_add_update_delete():
    conn = make_conn()
    conn.friend_apply_list = [100]

    asyncio.run(conn.handle_FriendChange(friend_change(100, 1, "新朋友")))
    asyncio.run(conn.handle_FriendChange(friend_change(100, 1, "新朋友")))
    assert list(conn.friends) == [100]
    assert conn.friend_apply_list == []

    asyncio.run(conn.handle_FriendChange(friend_change(100, 3, "改名了")))
    assert conn.friends[100].nickname == "改名了"

    asyncio.run(conn.handle_FriendChange(friend_change(100, 2)))
    asyncio.run(conn.handle_FriendChange(friend_change(100, 2)))
    assert conn.friends == {}
    assert conn.metas == [
        "账号成功添加好友 新朋友！",
        "账号已存在好友 新朋友！",
        "数据成功更新好友 改名了！",
        "账号成功删除好友 改名了！",
    ]


def test_update_for_unknown_friend_is_ignored():
    conn = make_conn()
    asyncio.run(conn.handle_FriendChange(friend_change(100, 3, "陌生人")))
    assert conn.friends == {}
    assert conn.metas == []


def test_new_friend_apply_without_brief_uses_account_id(monkeypatch: pytest.MonkeyPatch):
    # 之前 fetchMultiAccountBrief 返回空列表时直接取 players[0]，抛 IndexError
    conn = make_conn([liblq.ResMultiAccountBrief()])
    payload = liblq.NotifyNewFriendApply(account_id=77)
    notify = model.MajsoulDecodedMessage(msg_type=1, req_index=0, method_name="", payload=payload)
    config = types.SimpleNamespace(get_config=lambda key: types.SimpleNamespace(data=False))
    monkeypatch.setattr(majsoul, "MAJS_CONFIG", config)
    asyncio.run(conn.handle_NewFriendApply(notify))
    assert conn.friend_apply_list == [77]
    assert conn.metas == ["收到来自 77 的好友申请"]


def test_friend_change_while_iterating_does_not_break_loop():
    # 好友状态推送会在 await 中途遍历 friends，期间可能收到增删好友的通知
    conn = make_conn([fetch_info_resp(FRIENDS, [])])
    asyncio.run(conn.fetchInfo())
    seen = []
    for friend in conn.friends.values():
        seen.append(friend.account_id)
        if len(seen) == 1:
            asyncio.run(conn.handle_FriendChange(friend_change(100, 1, "新朋友")))
            asyncio.run(conn.handle_FriendChange(friend_change(218174, 2)))
    assert seen == list(FRIENDS)
    assert set(conn.friends) == {1194642, 17033013, 12536235, 100}
