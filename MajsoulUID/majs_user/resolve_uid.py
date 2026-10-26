from typing import NamedTuple

from ..utils.api.remote import decode_friend_code
from ..majs_notify.majsoul import MajsoulConnection, manager

UINT32_MAX = 0xFFFFFFFF
SEARCH_TIP = "不知道 UID 可以用[雀魂搜索 你的昵称]查"
FRIEND_CODE_TIP = f"\n注意：游戏里「我的ID」是好友码，不是 UID。{SEARCH_TIP}"


class BindTarget(NamedTuple):
    uid: str
    nickname: str | None
    note: str


async def _nickname(conn: MajsoulConnection, account_id: int) -> str | None:
    if not 0 < account_id <= UINT32_MAX:
        return None
    return await conn.fetchAccountNickname(account_id)


def _converted(raw: str) -> str:
    return f"\n{raw} 是游戏里「我的ID」（好友码），已换成对应的 UID"


async def resolve_bind_uid(raw: str) -> BindTarget:
    """玩家常把游戏里「我的ID」（好友码）当 UID 发来，用账号池把两种读法都查一遍"""
    conn = manager.get_conn()
    if conn is None or conn.is_closed():
        return BindTarget(raw, None, FRIEND_CODE_TIP)

    code_uid = decode_friend_code(int(raw))
    # 已是机器人好友的那种读法基本就是对的，不必再查、也不必再提示另一种读法
    friends = {
        aid: f for c in manager.get_all_conn() for aid, f in c.friends.items()
    }
    if int(raw) in friends:
        return BindTarget(raw, friends[int(raw)].nickname, "")
    if code_uid in friends:
        return BindTarget(
            str(code_uid), friends[code_uid].nickname, _converted(raw)
        )

    try:
        as_uid = await _nickname(conn, int(raw))
        as_code = await _nickname(conn, code_uid)
    except ConnectionError:
        # 账号池断线不该拦住绑定，退回不校验
        return BindTarget(raw, None, FRIEND_CODE_TIP)

    if as_uid and as_code:
        return BindTarget(
            raw,
            as_uid,
            f"\n不是你？游戏里「我的ID」是好友码，{raw} 当好友码看对应「{as_code}」（UID {code_uid}）。"
            f"是这个号就发[雀魂绑定uid{code_uid}]",
        )
    if as_code:
        return BindTarget(str(code_uid), as_code, _converted(raw))
    if as_uid:
        return BindTarget(raw, as_uid, "")
    return BindTarget(
        raw,
        None,
        f"\n账号池没查到这个 UID，可能不在同一个服，也可能填成了好友码。{SEARCH_TIP}",
    )
