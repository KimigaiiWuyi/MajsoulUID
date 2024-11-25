from typing import List
from collections import Counter


def normalize_tile(tile: str):
    """
    将赤宝牌（带 'r' 后缀的牌）转化为普通牌。
    """
    if tile.endswith("r"):
        return tile[:-1]
    return tile


def find_ting_tiles(hand: List[str]):
    """
    找到可以打出哪张牌后听牌。
    hand: list[str]，14张手牌。
    返回 dict，key 是打出的牌，value 是可以听的牌列表。
    """
    results = {}
    possible_tiles = [f"{i}{suit}" for i in range(1, 10) for suit in "mps"]
    possible_tiles += ["E", "S", "W", "N", "P", "F", "C"]
    possible_tiles += ["5mr", "5pr", "5sr"]  # 包含赤宝牌
    counts = Counter(map(normalize_tile, hand))  # 归一化手牌

    for tile in hand:
        normalized_tile = normalize_tile(tile)
        # 模拟打出这张牌
        counts[normalized_tile] -= 1
        if counts[normalized_tile] == 0:
            del counts[normalized_tile]
        ting_tiles = []

        # 枚举可能的摸牌
        for new_tile in possible_tiles:
            counts[normalize_tile(new_tile)] += 1
            if is_valid_hand(counts):
                ting_tiles.append(new_tile)
            counts[normalize_tile(new_tile)] -= 1

        # 恢复打出的牌
        counts[normalized_tile] += 1

        if ting_tiles:
            results[tile] = ting_tiles

    return results


def is_valid_hand(hand):
    """
    检查是否满足胡牌条件，包括普通胡牌和特殊牌型（小七对、国士无双）。
    """
    if is_standard_hand(hand):
        return True
    if is_seven_pairs(hand):
        return True
    if is_thirteen_orphans(hand):
        return True
    return False


def is_standard_hand(hand):
    """
    检查是否是普通牌型（雀头 + 四组顺子或刻子）。
    """
    for tile in hand:
        if hand[tile] >= 2:
            hand[tile] -= 2
            if can_form_melds(hand):
                hand[tile] += 2
                return True
            hand[tile] += 2
    return False


def can_form_melds(hand):
    """
    检查手牌是否可以被分解为四个顺子或刻子。
    """
    hand = Counter(hand)  # 深拷贝，避免修改原手牌
    for tile in sorted(hand):
        while hand[tile] >= 3:  # 尝试移除刻子
            hand[tile] -= 3
        # 尝试移除顺子
        if tile[0] in '123456789':  # 仅数牌适用顺子
            num, suit = int(tile[0]), tile[1]
            while (
                hand[tile] > 0
                and hand.get(f"{num+1}{suit}", 0) > 0
                and hand.get(f"{num+2}{suit}", 0) > 0
            ):
                hand[tile] -= 1
                hand[f"{num+1}{suit}"] -= 1
                hand[f"{num+2}{suit}"] -= 1
    return sum(hand.values()) == 0  # 所有牌都被配对完毕


def is_seven_pairs(hand):
    """
    检查是否是小七对牌型（7 对牌）。
    """
    return sum(v == 2 for v in hand.values()) == 7


def is_thirteen_orphans(hand):
    """
    检查是否是国士无双牌型。
    """
    required_tiles = {
        "1m",
        "9m",
        "1p",
        "9p",
        "1s",
        "9s",
        "E",
        "S",
        "W",
        "N",
        "P",
        "F",
        "C",
    }
    single_tiles = [tile for tile in required_tiles if hand.get(tile, 0) > 0]
    pair_tiles = [tile for tile in single_tiles if hand.get(tile, 0) >= 2]
    a = len(single_tiles) == 13
    b = len(single_tiles) == 12
    c = len(pair_tiles) == 1
    return a or (b and c)
