from typing import List, Tuple, Optional

from ..utils.majs_api import majs_api
from ..utils.api.remote import PlayerLevel


async def search_player_with_name(name: str) -> Tuple[str, Optional[List]]:
    data = await majs_api.search_player(name.strip())
    msg_list = []
    uid_list = []
    for player in data:
        msg_list.append(
            f'玩家: {player["nickname"]} (ID: {player["id"]})\n'
            f'段位：{PlayerLevel(player["level"]["id"]).getTag()}'
        )
        uid_list.append(player['id'])
    if msg_list == []:
        return (
            '暂未搜索到该玩家ID噢~\n提示: 需要在金之间有一定数量的对局才能被搜索到！',
            None,
        )

    return (
        '\n'.join(msg_list) + '\n提示：可使用[雀魂绑定+你的ID]进行角色绑定',
        uid_list,
    )
