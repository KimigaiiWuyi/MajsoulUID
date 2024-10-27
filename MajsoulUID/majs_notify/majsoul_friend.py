from ..lib import lq as liblq
from ._level import MajsoulLevel


class MajsoulFriend:
    def __init__(self, friend: liblq.Friend):
        self.player = friend
        self.account_id = friend.base.account_id
        self.nickname = friend.base.nickname
        self.avatar_id = friend.base.avatar_id

        self.level = MajsoulLevel(friend.base.level.id)
        self.level_score = friend.base.level.score
        self.level3 = MajsoulLevel(friend.base.level3.id)
        self.level3_score = friend.base.level3.score
        self.login_time = friend.state.login_time
        self.logout_time = friend.state.logout_time
        self.is_online = friend.state.is_online
        self.playing = friend.state.playing

    def change_base(self, base: liblq.PlayerBaseView):
        self.nickname = base.nickname
        self.level = MajsoulLevel(base.level.id)
        self.level_score = base.level.score
        self.level3 = MajsoulLevel(base.level3.id)
        self.level3_score = base.level3.score

    def change_state(self, state: liblq.AccountActiveState):
        self.login_time = state.login_time
        self.logout_time = state.logout_time
        self.is_online = state.is_online
        self.playing = state.playing
