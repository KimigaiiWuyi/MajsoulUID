from copy import deepcopy
from typing import List, Union


def split_list(lst: str):
    return [lst[i : i + 2] for i in range(0, len(lst), 2)]  # noqa:E203


def sort_list(lst: List[List[int]], now_seat: int, pos: int):
    if pos == 0:
        lst.append([0, 1, 2, 3])
    elif pos == 2:
        if now_seat == 0:
            lst.pop()
            lst.append([0, 2, 0, 1, 2, 3])
        elif now_seat == 1:
            lst.append([0, 1, 2, 3, 1, 2, 3])
        elif now_seat == 2:
            lst.append([0, 2, 3])
        else:  # elif now_seat == 3:
            lst.append([0, 1, 3])
    else:  # elif pos == 4:
        if now_seat == 0:
            lst.pop()
            lst.append([0, 1, 0, 1, 2, 3])
        elif now_seat == 1:
            lst.append([0, 1, 2, 1, 2, 3])
        elif now_seat == 2:
            lst.append([0, 1, 2, 3, 2, 3])
        else:  # elif now_seat == 3:
            lst.append([0, 3])
    return lst


def n2p(number: int):
    if 47 >= number >= 41:
        return 'ESWNPFC'[number - 41]
    elif 39 >= number >= 31:
        return [f'{i}s' for i in range(1, 10)][number - 31]
    elif 29 >= number >= 21:
        return [f'{i}p' for i in range(1, 10)][number - 21]
    elif 19 >= number >= 11:
        return [f'{i}m' for i in range(1, 10)][number - 11]
    elif number == 51:
        return '5mr'
    elif number == 52:
        return '5pr'
    elif number == 53:
        return '5sr'
    return None


class MeguruLog:
    def __init__(self, log: List[List[Union[str, int]]], _target_actor: int):
        self.log = log
        self._target_actor = _target_actor
        self.now_state = log[0]
        self.kyoku_id = self.now_state[0]
        self.honba_id = self.now_state[1]
        self.ricchi_hon = self.now_state[2]
        self.result = log[-1]
        self.dora_list = log[2]
        self.uradora_list = log[3]
        self.player_state = [
            [log[4 + i * 3], log[5 + i * 3], log[6 + i * 3]] for i in range(4)
        ]
        self.frame = []

    def process(self):
        point = {
            0: 0,
            1: 0,
            2: 0,
            3: 0,
        }
        max_num = 0
        for i in self.player_state:
            max_num += len(i[1])

        result_condi = []

        for index in range(max_num // 4):
            for j in [0, 1, 2, 3]:
                if point[j] >= len(self.player_state[j][1]):
                    continue
                _draw = self.player_state[j][1][point[j]]
                point[j] += 1
                if isinstance(_draw, str):
                    if 'p' in _draw:
                        num = _draw.index('p')
                    elif 'c' in _draw:
                        num = _draw.index('c')
                    else:
                        num = _draw.index('k')
                    result_condi = sort_list(result_condi, j, num)
                    break
            else:
                result_condi.append([0, 1, 2, 3])

        result = [item for sublist in result_condi for item in sublist]
        point = {
            0: 0,
            1: 0,
            2: 0,
            3: 0,
        }
        furoos = {
            0: [],
            1: [],
            2: [],
            3: [],
        }
        discard = {
            0: {},
            1: {},
            2: {},
            3: {},
        }
        a = 0
        for seat_num in result:
            a += 1
            now_seat = self.player_state[seat_num]
            # 第几巡
            now_action = now_seat[1][point[seat_num]]
            now_discard = now_seat[2][point[seat_num]]

            point[seat_num] += 1
            if isinstance(now_action, int):
                now_seat[0].append(now_action)
            else:
                if 'c' in now_action:
                    now_action = now_action.replace('c', '')
                    furoos[seat_num].append(split_list(now_action))
                elif 'k' in now_action:
                    now_action = now_action.replace('k', '')
                    furoos[seat_num].append(split_list(now_action))

            if isinstance(now_discard, int):
                if now_discard == 60:
                    discard[seat_num][now_action] = 1
                else:
                    now_seat[0].remove(now_discard)
                    discard[seat_num][now_discard] = 0

            if seat_num == self._target_actor:
                _d = deepcopy(discard)
                self.frame.append(_d)
        return self.frame
