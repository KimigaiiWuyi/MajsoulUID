from ..lib import lq as liblq
from ..utils.api.remote import PlayerLevel


class MajsoulFriend:
    def __init__(self, friend: liblq.Friend):
        self.player = friend
        self.account_id = friend.base.account_id
        self.nickname = friend.base.nickname
        self.level = PlayerLevel(friend.base.level.id, friend.base.level.score)

        self.level3 = PlayerLevel(
            friend.base.level3.id, friend.base.level3.score
        )
        self.login_time = friend.state.login_time
        self.logout_time = friend.state.logout_time
        self.is_online = friend.state.is_online
        self.playing = friend.state.playing

    def change_base(self, base: liblq.PlayerBaseView):
        self.nickname = base.nickname
        self.level = PlayerLevel(base.level.id, base.level.score)
        self.level3 = PlayerLevel(base.level3.id, base.level3.score)

    def change_state(self, state: liblq.AccountActiveState):
        self.login_time = state.login_time
        self.logout_time = state.logout_time
        self.is_online = state.is_online
        self.playing = state.playing
