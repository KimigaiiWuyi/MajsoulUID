import sys
import types
import asyncio
import importlib
from pathlib import Path

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

# 好友总览截图里的四个人
FRIENDS = {1194642: "烟树历历", 17033013: "兰潇澜", 12536235: "浅碎丶时空", 218174: "雨滴答滴答"}


def make_friend(account_id: int, nickname: str):
    level = liblq.AccountLevel(id=10301)
    return liblq.Friend(base=liblq.PlayerBaseView(account_id=account_id, nickname=nickname, level=level, level3=level))


def make_conn(responses: list | None = None):
    route = types.SimpleNamespace(ssl=True, domain="example.invalid")
    conn = majsoul.MajsoulConnection(route, None, 0, None, "device")
    conn.metas = []

    async def rpc_call(method_name: str, payload: dict):
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
