import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from lib.lq import (
    HuleInfo,
    RecordAnGangAddGang,
    RecordBaBei,
    RecordChiPengGang,
    RecordDealTile,
    RecordDiscardTile,
    RecordHule,
    RecordLiuJu,
    RecordNewRound,
    RecordNoTile,
)
from model import MjsLog, MjsLogItem

with open("data.json", "r", encoding="utf8") as f:
    cfg = json.load(f)

# variables you might actually want to change
NAMEPREF = 0  # 2 for english, 1 for sane amount of weeb, 0 for japanese
# VERBOSELOG = False  # dump mjs records to output - will make the file too large for tenhou.net/5 viewer
SHOWFU = False  # always show fu/han for scoring - even for limit hands

JPNAME = 0
RUNES = {
    # hand limits
    "mangan": ["満貫", "Mangan ", "Mangan "],
    "haneman": ["跳満", "Haneman ", "Haneman "],
    "baiman": ["倍満", "Baiman ", "Baiman "],
    "sanbaiman": ["三倍満", "Sanbaiman ", "Sanbaiman "],
    "yakuman": ["役満", "Yakuman ", "Yakuman "],
    "kazoeyakuman": ["数え役満", "Kazoe Yakuman ", "Counted Yakuman "],
    "kiriagemangan": ["切り上げ満貫", "Kiriage Mangan ", "Rounded Mangan "],
    # round enders
    "agari": ["和了", "Agari", "Agari"],
    "ryuukyoku": ["流局", "Ryuukyoku", "Exhaustive Draw"],
    "nagashimangan": ["流し満貫", "Nagashi Mangan", "Mangan at Draw"],
    "suukaikan": ["四開槓", "Suukaikan", "Four Kan Abortion"],
    "sanchahou": ["三家和", "Sanchahou", "Three Ron Abortion"],
    "kyuushukyuuhai": ["九種九牌", "Kyuushu Kyuuhai", "Nine Terminal Abortion"],
    "suufonrenda": ["四風連打", "Suufon Renda", "Four Wind Abortion"],
    "suuchariichi": ["四家立直", "Suucha Riichi", "Four Riichi Abortion"],
    # scoring
    "fu": ["符", "符", "Fu"],
    "han": ["飜", "飜", "Han"],
    "points": ["点", "点", "Points"],
    "all": ["∀", "∀", "∀"],
    "pao": ["包", "pao", "Responsibility"],
    # rooms
    "tonpuu": ["東喰", " East", " East"],
    "hanchan": ["南喰", " South", " South"],
    "friendly": ["友人戦", "Friendly", "Friendly"],
    "tournament": ["大会戦", "Tounament", "Tournament"],
    "sanma": ["三", "3-Player ", "3-Player "],
    "red": ["赤", " Red", " Red Fives"],
    "nored": ["", " Aka Nashi", " No Red Fives"],
}

# senkinin barai yaku - please don't change, yostar..
DAISANGEN = 37  # daisangen cfg.fan.fan.map_ index
DAISUUSHI = 50

TSUMOGIRI = 60  # tenhou tsumogiri symbol

# global variables - don't touch
ALLOW_KIRIAGE = False  # potentially allow this to be true
TSUMOLOSSOFF = False  # sanma tsumo loss, is set true for sanma when tsumo loss off


def pad_right(a: list[int], l: int, f: int):
    return a + [f] * (l - len(a))


def tm2t(s: str):
    """
    Convert tile string '2m' to Tenhou's tile encoding.
    11-19 - 1-9 man
    21-29 - 1-9 pin
    31-39 - 1-9 sou
    41-47 - ESWN WGR
    51,52,53 - aka 5 man, pin, sou
    """
    num = int(s[0])
    tcon = {"m": 1, "p": 2, "s": 3, "z": 4}

    return 10 * tcon[s[1]] + num if num else 50 + tcon[s[1]]


def deaka(til: int):
    """
    Return normal tile from aka version, Tenhou representation.
    """
    if til // 10 == 5:
        return 10 * (til % 10) + (til // 10)
    return til


def makeaka(til: int):
    """
    Return aka version of tile.
    """
    if til % 10 == 5:  # is a five (or haku)
        return 10 * (til % 10) + (til // 10)
    return til


def tlround(x: int | float):
    """
    Round up to nearest hundred if TSUMOLOSSOFF is True, otherwise return 0.
    """
    return 100 * -(-int(x) // 100) if TSUMOLOSSOFF else 0


kyoku = None


class Kyoku:
    def __init__(self, leaf: RecordNewRound):
        # Number of players and round information [kyoku, honba, riichi sticks]
        self.nplayers = len(leaf.scores)
        self.round: list[int] = [
            4 * leaf.chang + leaf.ju,
            leaf.ben,
            leaf.liqibang,
        ]
        self.initscores: list[int] = leaf.scores[:]
        self.initscores = pad_right(self.initscores, 4, 0)

        # Dora indicators
        self.doras: list[int] = [tm2t(d) for d in leaf.doras]

        # Draws and discards for each player
        self.draws: list[list[int | str]] = [[] for _ in range(4)]
        self.discards: list[list[int | str]] = [[] for _ in range(4)]

        # Haipais (starting hand for each player)
        tiles0 = list(map(tm2t, leaf.tiles0))
        tiles1 = list(map(tm2t, leaf.tiles1))
        tiles2 = list(map(tm2t, leaf.tiles2))
        tiles3 = list(map(tm2t, leaf.tiles3))
        self.haipais: list[list[int]] = [tiles0, tiles1, tiles2, tiles3]

        # Treat the last tile in the dealer's hand as a drawn tile
        self.poppedtile = self.haipais[leaf.ju].pop()
        self.draws[leaf.ju].append(self.poppedtile)
        print(f"self.draws: {self.draws}")

        # Additional game information
        self.dealerseat = leaf.ju
        self.ldseat = -1  # Who dealt the last tile
        self.nriichi = 0  # Number of current riichis
        self.priichi = False
        self.nkan = 0  # Number of current kans

        # Pao rule (wind and dragon counters)
        self.nowinds = [0] * 4
        self.nodrags = [0] * 4
        self.paowind = -1
        self.paodrag = -1

    def dump(self, uras: list[int]):
        """Dump the round information."""
        entry: list[Sequence[int | str]] = []
        entry.append(self.round)
        entry.append(self.initscores)
        entry.append(self.doras)
        entry.append(uras)

        for i in range(4):
            entry.append(self.haipais[i])
            entry.append(self.draws[i])
            entry.append(self.discards[i])

        return entry

    def countpao(self, tile: int, owner: int, feeder: int):
        """Senkinin barai incrementer, called on pon, daiminkan, ankan."""
        if tile in WINDS:
            self.nowinds[owner] += 1
            if self.nowinds[owner] == 4:
                self.paowind = feeder
        elif tile in DRAGS:
            self.nodrags[owner] += 1
            if self.nodrags[owner] == 3:
                self.paodrag = feeder


def parsehule(h: HuleInfo, kyoku: Kyoku, is_head_bump: bool):
    """
    Parse MJS hule into Tenhou agari list
    """
    # Tenhou log viewer requires 点, 飜) or 役満) to end strings, rest of scoring string is optional
    res: list[int | str] = [
        h.seat,
        h.zimo and h.seat or kyoku.ldseat,
        h.seat,
    ]
    delta: list[int] = []  # to handle double/triple ron
    points = 0
    rp = (
        1000 * (kyoku.nriichi + kyoku.round[2]) if is_head_bump else 0
    )  # riichi stick points
    hb = 100 * kyoku.round[1] if is_head_bump else 0  # base honba payment

    # sekinin barai logic
    pao = False
    liable_seat = -1
    liable_for: int = 0

    if h.yiman:
        for e in h.fans:
            if e.id == DAISUUSHI and kyoku.paowind != -1:
                pao = True
                liable_seat = kyoku.paowind
                liable_for += e.val  # can be liable only once
            elif e.id == DAISANGEN and kyoku.paodrag != -1:
                pao = True
                liable_seat = kyoku.paodrag
                liable_for += e.val

    if h.zimo:
        # ko-oya payment for non-dealer tsumo
        delta = [
            -hb - h.point_zimo_xian - tlround(0.5 * h.point_zimo_xian)
        ] * kyoku.nplayers

        if h.seat == kyoku.dealerseat:  # oya tsumo
            delta[h.seat] = (
                rp
                + (kyoku.nplayers - 1) * (hb + h.point_zimo_xian)
                + 2 * tlround(0.5 * h.point_zimo_xian)
            )
            points = h.point_zimo_xian + tlround(0.5 * h.point_zimo_xian)
        else:  # ko tsumo
            delta[h.seat] = (
                rp
                + hb
                + h.point_zimo_qin
                + (kyoku.nplayers - 2) * (hb + h.point_zimo_xian)
                + 2 * tlround(0.5 * h.point_zimo_xian)
            )
            delta[kyoku.dealerseat] = (
                -hb - h.point_zimo_qin - tlround(0.5 * h.point_zimo_xian)
            )
            points = f"{h.point_zimo_xian}-{h.point_zimo_qin}"
    else:
        # ron
        delta = [0] * kyoku.nplayers
        delta[h.seat] = rp + (kyoku.nplayers - 1) * hb + h.point_rong
        delta[kyoku.ldseat] = -(kyoku.nplayers - 1) * hb - h.point_rong
        points = h.point_rong

    # sekinin barai payments
    OYA = 0
    KO = 1
    RON = 2
    YSCORE = [
        [0, 16000, 48000],  # oya wins
        [16000, 8000, 32000],  # ko wins
    ]

    if pao:
        res[2] = liable_seat

        if h.zimo:
            if h.fu:  # dealer tsumo
                delta[liable_seat] -= (
                    2 * hb
                    + liable_for * 2 * YSCORE[OYA][KO]
                    + tlround(0.5 * liable_for * YSCORE[OYA][KO])
                )
                for i in range(kyoku.nplayers):
                    if liable_seat != i and h.seat != i and kyoku.nplayers >= i:
                        delta[i] += (
                            hb
                            + liable_for * YSCORE[OYA][KO]
                            + tlround(0.5 * liable_for * YSCORE[OYA][KO])
                        )
                if kyoku.nplayers == 3:  # dealer gets north's payment from liable
                    delta[h.seat] += 0 if TSUMOLOSSOFF else liable_for * YSCORE[OYA][KO]
            else:  # non-dealer tsumo
                delta[liable_seat] -= (
                    (kyoku.nplayers - 2) * hb
                    + liable_for * (YSCORE[KO][OYA] + YSCORE[KO][KO])
                    + tlround(0.5 * liable_for * YSCORE[KO][KO])
                )
                for i in range(kyoku.nplayers):
                    if liable_seat != i and h.seat != i and kyoku.nplayers >= i:
                        if kyoku.dealerseat == i:
                            delta[i] += (
                                hb
                                + liable_for * YSCORE[KO][OYA]
                                + tlround(0.5 * liable_for * YSCORE[KO][KO])
                            )
                        else:
                            delta[i] += (
                                hb
                                + liable_for * YSCORE[KO][KO]
                                + tlround(0.5 * liable_for * YSCORE[KO][KO])
                            )
        else:
            delta[liable_seat] -= int(
                (kyoku.nplayers - 1) * hb
                + 0.5 * liable_for * YSCORE[OYA if h.fu else KO][RON]
            )
            delta[kyoku.ldseat] += int(
                (kyoku.nplayers - 1) * hb
                + 0.5 * liable_for * YSCORE[OYA if h.fu else KO][RON]
            )

    # append point symbol
    points = (
        str(points)
        + str(RUNES["points"][JPNAME])
        + str(RUNES["all"][NAMEPREF] if h.zimo and h.fu else "")
    )

    # score string
    fuhan = f"{h.fu}{RUNES['fu'][NAMEPREF]}{h.count}{RUNES['han'][NAMEPREF]}"
    if h.yiman:
        res.append(f"{fuhan if SHOWFU else ''}{RUNES['yakuman'][NAMEPREF]}{points}")
    elif h.count >= 13:
        res.append(
            f"{fuhan if SHOWFU else ''}{RUNES['kazoeyakuman'][NAMEPREF]}{points}"
        )
    elif h.count >= 11:
        res.append(f"{fuhan if SHOWFU else ''}{RUNES['sanbaiman'][NAMEPREF]}{points}")
    elif h.count >= 8:
        res.append(f"{fuhan if SHOWFU else ''}{RUNES['baiman'][NAMEPREF]}{points}")
    elif h.count >= 6:
        res.append(f"{fuhan if SHOWFU else ''}{RUNES['haneman'][NAMEPREF]}{points}")
    elif h.count >= 5 or (h.count >= 4 and h.fu >= 40) or (h.count >= 3 and h.fu >= 70):
        res.append(f"{fuhan if SHOWFU else ''}{RUNES['mangan'][NAMEPREF]}{points}")
    elif ALLOW_KIRIAGE and (
        (h.count == 4 and h.fu == 30) or (h.count == 3 and h.fu == 60)
    ):
        res.append(
            f"{fuhan if SHOWFU else ''}{RUNES['kiriagemangan'][NAMEPREF]}{points}"
        )
    else:
        res.append(f"{fuhan}{points}")

    for e in h.fans:
        name_jp = cfg["fan"]["fan"]["map_"][str(e.id)]["name_jp"]
        name_en = cfg["fan"]["fan"]["map_"][str(e.id)]["name_en"]
        if h.yiman:
            res.append(
                f"{name_jp if JPNAME == NAMEPREF else name_en}({RUNES['yakuman'][JPNAME]})"
            )
        else:
            res.append(
                f"{name_jp if JPNAME == NAMEPREF else name_en}({e.val}{RUNES['han'][JPNAME]})"
            )

    return [pad_right(delta, 4, 0), res]


# Sekinin barai tiles
WINDS = list(map(lambda e: tm2t(e), ["1z", "2z", "3z", "4z"]))
DRAGS = list(map(lambda e: tm2t(e), ["5z", "6z", "7z", "0z"]))  # 0z represents aka haku


def relativeseating(seat0: int, seat1: int):
    """Determine the relative seating between seat0 and seat1."""
    # 0: kamicha, 1: toimen, 2: shimocha
    return (seat0 - seat1 + 4 - 1) % 4


def generatelog(mjslog: list[MjsLogItem]):
    global kyoku
    log = []

    for item in mjslog:
        e = item.data
        match item.name:
            case "RecordNewRound":
                e = cast(RecordNewRound, e)
                kyoku = Kyoku(e)

            case "RecordDiscardTile":
                assert kyoku is not None
                e = cast(RecordDiscardTile, e)
                # Discard - marking tsumogiri and riichi
                symbol = TSUMOGIRI if e.moqie else tm2t(e.tile)

                # We pretend that the dealer's initial 14th tile is drawn - manually check the first discard
                if (
                    e.seat == kyoku.dealerseat
                    and not kyoku.discards[e.seat]
                    and symbol == kyoku.poppedtile
                ):
                    symbol = TSUMOGIRI

                if e.is_liqi:  # Riichi declaration
                    kyoku.priichi = True
                    symbol = "r" + str(symbol)

                kyoku.discards[e.seat].append(symbol)
                kyoku.ldseat = e.seat  # For ron, pon, etc.

                # Sometimes we get dora passed here
                if e.doras and len(e.doras) > len(kyoku.doras):
                    data = [tm2t(d) for d in e.doras]
                    kyoku.doras = data

            case "RecordDealTile":
                assert kyoku is not None
                e = cast(RecordDealTile, e)
                # Draw - after kan this gets passed the new dora
                if kyoku.priichi:
                    kyoku.priichi = False
                    kyoku.nriichi += 1

                if e.doras and len(e.doras) > len(kyoku.doras):
                    kyoku.doras = [tm2t(d) for d in e.doras]

                kyoku.draws[e.seat].append(tm2t(e.tile))

            case "RecordChiPengGang":
                assert kyoku is not None
                e = cast(RecordChiPengGang, e)
                # Call - chi, pon, daiminkan
                if kyoku.priichi:
                    kyoku.priichi = False
                    kyoku.nriichi += 1

                if e.type == 0:  # Chi
                    kyoku.draws[e.seat].append(
                        "c"
                        + str(tm2t(e.tiles[2]))
                        + str(tm2t(e.tiles[0]))
                        + str(tm2t(e.tiles[1]))
                    )

                elif e.type == 1:  # Pon
                    worktiles: list[str | int] = [tm2t(t) for t in e.tiles]
                    idx = relativeseating(e.seat, kyoku.ldseat)
                    if not isinstance(worktiles[0], int):
                        raise ValueError("Pon tile is not an integer")
                    kyoku.countpao(worktiles[0], e.seat, kyoku.ldseat)
                    worktiles.insert(idx, "p" + str(worktiles.pop()))
                    tmp = ""
                    for data in worktiles:
                        tmp += str(data)
                    kyoku.draws[e.seat].append(tmp)

                elif e.type == 2:  # Daiminkan
                    calltiles: list[str | int] = [tm2t(t) for t in e.tiles]
                    idx = relativeseating(e.seat, kyoku.ldseat)
                    if not isinstance(calltiles[0], int):
                        raise ValueError("Daiminkan tile is not an integer")
                    kyoku.countpao(calltiles[0], e.seat, kyoku.ldseat)
                    calltiles.insert(3 if idx == 2 else idx, "m" + str(calltiles.pop()))
                    tmp = ""
                    for data in calltiles:
                        tmp += str(data)
                    print(tmp)
                    kyoku.draws[e.seat].append(tmp)
                    kyoku.discards[e.seat].append(
                        0
                    )  # Tenhou drops a 0 in discards for this
                    kyoku.nkan += 1

            case "RecordAnGangAddGang":
                assert kyoku is not None
                e = cast(RecordAnGangAddGang, e)
                # Kan - shouminkan 'k', ankan 'a'
                til = tm2t(e.tiles)
                kyoku.ldseat = e.seat

                if e.type == 3:  # Ankan
                    kyoku.countpao(til, e.seat, -1)
                    ankantiles = [
                        t for t in kyoku.haipais[e.seat] if deaka(t) == deaka(til)
                    ] + [t for t in kyoku.draws[e.seat] if deaka(int(t)) == deaka(til)]
                    til = str(ankantiles.pop())
                    tmp = ""
                    for data in ankantiles:
                        tmp += str(data)
                    kyoku.discards[e.seat].append(tmp + "a" + til)
                    kyoku.nkan += 1

                elif e.type == 2:  # Shouminkan
                    nakis = [
                        w
                        for w in kyoku.draws[e.seat]
                        if isinstance(w, str)
                        and ("p" + str(deaka(til)) in w or "p" + str(makeaka(til)) in w)
                    ]
                    kyoku.discards[e.seat].append(nakis[0].replace("p", "k" + str(til)))
                    kyoku.nkan += 1

            case "RecordBaBei":
                assert kyoku is not None
                e = cast(RecordBaBei, e)
                # Kita
                kyoku.discards[e.seat].append("f44")

            case "RecordLiuJu":
                assert kyoku is not None
                e = cast(RecordLiuJu, e)
                # Abortion
                if kyoku.priichi:
                    kyoku.priichi = False
                    kyoku.nriichi += 1

                entry = kyoku.dump([])
                if e.type == 1:
                    entry.append([RUNES["kyuushukyuuhai"][NAMEPREF]])  # Kyuushukyuhai
                elif e.type == 2:
                    entry.append([RUNES["suufonrenda"][NAMEPREF]])  # Suufon renda
                elif kyoku.nriichi == 4:
                    entry.append([RUNES["suuchariichi"][NAMEPREF]])  # 4 riichi
                elif kyoku.nkan >= 4:
                    entry.append([RUNES["suukaikan"][NAMEPREF]])  # 4 kan
                else:
                    entry.append([RUNES["sanchahou"][NAMEPREF]])  # 3 ron
                log.append(entry)

            case "RecordNoTile":
                assert kyoku is not None
                e = cast(RecordNoTile, e)
                # Ryuukyoku
                entry = kyoku.dump([])
                delta = [0] * 4

                if e.scores and e.scores[0].delta_scores:
                    for f in e.scores:
                        for i, g in enumerate(f.delta_scores):
                            delta[i] += g

                if e.liujumanguan:
                    entry.append(
                        [RUNES["nagashimangan"][NAMEPREF], delta]
                    )  # Nagashi mangan
                else:
                    entry.append(
                        [RUNES["ryuukyoku"][NAMEPREF], delta]
                    )  # Normal ryuukyoku
                log.append(entry)

            case "RecordHule":
                assert kyoku is not None
                e = cast(RecordHule, e)
                # Agari
                agari = []
                ura = []
                isHeadBump = True

                for f in e.hules:
                    if len(ura) < len(f.li_doras or []):
                        ura = [tm2t(g) for g in f.li_doras or []]
                    agari.append(parsehule(f, kyoku, isHeadBump))
                    isHeadBump = False

                entry = kyoku.dump(ura)
                entry.append(
                    [RUNES["agari"][JPNAME]]
                    + [item for sublist in agari for item in sublist]
                )
                log.append(entry)

            case _:
                print(f"Didn't know what to do with {item.name}, {e}")

    return log


def toTenhou(record: MjsLog) -> dict[str, Any]:
    global TSUMOLOSSOFF
    res = {}
    ruledisp = ""
    lobby = ""
    nplayers = len(record.head.result.players)
    nakas = nplayers - 1
    mjslog = record.data

    res["ver"] = "2.3"
    res["ref"] = record.head.uuid
    res["log"] = generatelog(mjslog)

    res["ratingc"] = "PF" + str(nplayers)

    if nplayers == 3 and JPNAME == NAMEPREF:
        ruledisp += RUNES["sanma"][NAMEPREF]

    if record.head.config.meta.mode_id:
        if JPNAME == NAMEPREF:
            ruledisp += cfg["desktop"]["matchmode"]["map_"][
                str(record.head.config.meta.mode_id)
            ]["room_name_jp"]
        else:
            ruledisp += cfg["desktop"]["matchmode"]["map_"][
                str(record.head.config.meta.mode_id)
            ]["room_name_en"]
    elif record.head.config.meta.room_id:
        lobby = ": " + str(record.head.config.meta.room_id)
        ruledisp += RUNES["friendly"][NAMEPREF]
        nakas = record.head.config.mode.detail_rule.dora_count
        TSUMOLOSSOFF = (
            nplayers == 3
        ) and not record.head.config.mode.detail_rule.have_zimosun
    elif record.head.config.meta.contest_uid:
        lobby = ": " + str(record.head.config.meta.contest_uid)
        ruledisp += RUNES["tournament"][NAMEPREF]
        nakas = record.head.config.mode.detail_rule.dora_count
        TSUMOLOSSOFF = (
            nplayers == 3
        ) and not record.head.config.mode.detail_rule.have_zimosun

    if record.head.config.mode.mode == 1:
        ruledisp += RUNES["tonpuu"][NAMEPREF]
    elif record.head.config.mode.mode == 2:
        ruledisp += RUNES["hanchan"][NAMEPREF]

    if (
        not record.head.config.meta.mode_id
        and not record.head.config.mode.detail_rule.dora_count
    ):
        if JPNAME != NAMEPREF:
            ruledisp += RUNES["nored"][NAMEPREF]
        res["rule"] = {"disp": ruledisp, "aka53": 0, "aka52": 0, "aka51": 0}
    else:
        if JPNAME == NAMEPREF:
            ruledisp += RUNES["red"][NAMEPREF]
        res["rule"] = {
            "disp": ruledisp,
            "aka53": 1,
            "aka52": 2 if nakas == 4 else 1,
            "aka51": 1 if nplayers == 4 else 0,
        }

    res["lobby"] = 0
    res["dan"] = [""] * 4
    for e in record.head.accounts:
        if JPNAME == NAMEPREF:
            res["dan"][e.seat] = cfg["level_definition"]["level_definition"]["map_"][
                str(e.level.id)
            ]["full_name_jp"]
        else:
            res["dan"][e.seat] = cfg["level_definition"]["level_definition"]["map_"][
                str(e.level.id)
            ]["full_name_en"]

    res["rate"] = [0] * 4
    for e in record.head.accounts:
        res["rate"][e.seat] = e.level.score

    res["sx"] = ["C"] * 4
    res["name"] = ["AI"] * 4
    for e in record.head.accounts:
        res["name"][e.seat] = e.nickname

    if nplayers == 3:
        res["name"][3] = ""
        res["sx"][3] = ""

    scores = [
        (e.seat, e.part_point_1, int(e.total_point / 1000))
        for e in record.head.result.players
    ]
    res["sc"] = [0] * 8
    for e in scores:
        res["sc"][2 * e[0]] = e[1]
        res["sc"][2 * e[0] + 1] = e[2]

    res["title"] = [
        ruledisp + lobby,
        datetime.fromtimestamp(record.head.end_time).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        ),
    ]

    # if VERBOSELOG:
    #     res["mjshead"] = record.head
    #     res["mjslog"] = mjslog
    #     res["mjsrecordtypes"] = [e["__class__"]["name"] for e in mjslog]

    return res


# with open("mjslog copy.json", "r", encoding="utf8") as f:
#     mjslog = json.load(f)

# data = parse(mjslog)

# with open("output_py.json", "w", encoding="utf8") as f:
#     json.dump(data, f, ensure_ascii=False, indent=4)
