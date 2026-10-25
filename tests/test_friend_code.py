import importlib

import pytest

remote = importlib.import_module("gsuid_core.plugins.MajsoulUID.MajsoulUID.utils.api.remote")


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
