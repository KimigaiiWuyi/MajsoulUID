from math import ceil
from typing import cast
from datetime import datetime

from .cfg import cfg
from ..model import MjsLog, MjsLogItem
from .utils import pad_list, relative_seating
from .constants import RUNES, JPNAME, YSCORE, DAISANGEN, DAISUUSHI
from ...lib.lq import (
    HuleInfo,
    RecordHule,
    RecordBaBei,
    RecordLiuJu,
    RecordNoTile,
    RecordDealTile,
    RecordNewRound,
    RecordChiPengGang,
    RecordDiscardTile,
    RecordAnGangAddGang,
)
from .model import (
    Tile,
    Yaku,
    Agari,
    Kyoku,
    Round,
    PeSymbol,
    Ryukyoku,
    TileType,
    ChiSymbol,
    PonSymbol,
    AgariPoint,
    ZeroSymbol,
    AnkanSymbol,
    KakanSymbol,
    SingleAgari,
    DiscardSymbol,
    DaiminkanSymbol,
    SpecialRyukyoku,
)


class MajsoulPaipuParser:
    def __init__(
        self, *, tsumoloss_off: bool = False, allow_kigiage: bool = False
    ):
        self.kyokus = []

        self.tsumoloss_off = tsumoloss_off
        self.allow_kigiage = allow_kigiage

    def handle_game_record(self, record: MjsLog):
        res = {}
        ruledisp = ""
        lobby = ""  # usually 0, is the custom lobby number
        nplayers = len(record.head.result.players)
        nakas = nplayers - 1  # default
        # tsumoloss_off = False

        res["ver"] = "2.3"  # mlog version number
        res["ref"] = (
            record.head.uuid
        )  # game id - copy and paste into "other" on the log page to view

        # PF4 is yonma, PF3 is sanma
        res["ratingc"] = f"PF{nplayers}"

        # rule display
        if nplayers == 3:
            ruledisp += RUNES["sanma"][JPNAME]

        if record.head.config.meta.mode_id:  # ranked or casual
            ruledisp += cfg["desktop"]["matchmode"]["map_"][
                str(record.head.config.meta.mode_id)
            ]["room_name_jp"]
        elif record.head.config.meta.room_id:  # friendly
            # can set room number as lobby number
            lobby = f": {record.head.config.meta.room_id}"
            ruledisp += RUNES["friendly"][JPNAME]  # "Friendly"
            nakas = record.head.config.mode.detail_rule.dora_count
            """
            tsumoloss_off = (
                nplayers == 3
                and not record.head.config.mode.detail_rule.have_zimosun
            )
            """
        elif record.head.config.meta.contest_uid:  # tourney
            lobby = f": {record.head.config.meta.contest_uid}"
            ruledisp += RUNES["tournament"][JPNAME]  # "Tournament"
            nakas = record.head.config.mode.detail_rule.dora_count
            """
            tsumoloss_off = (
                nplayers == 3
                and not record.head.config.mode.detail_rule.have_zimosun
            )
            """
        if record.head.config.mode.mode == 1:
            ruledisp += RUNES["tonpuu"][JPNAME]  # " East"
        elif record.head.config.mode.mode == 2:
            ruledisp += RUNES["hanchan"][JPNAME]

        if (
            record.head.config.meta.mode_id == 0
            and record.head.config.mode.detail_rule.dora_count == 0
        ):
            res["rule"] = {
                "disp": ruledisp,
                "aka53": 0,
                "aka52": 0,
                "aka51": 0,
            }
        else:
            res["rule"] = {
                "disp": ruledisp,
                "aka53": 1,
                "aka52": 2 if nakas == 4 else 1,
                "aka51": 1 if nplayers == 4 else 0,
            }

        # tenhou custom lobby
        # could be tourney id or friendly room for
        # mjs. appending to title instead to avoid 3->C etc. in tenhou.net/5
        res["lobby"] = 0

        # autism to fix logs with AI
        # ranks
        res["dan"] = [""] * nplayers
        for e in record.head.accounts:
            res["dan"][e.seat] = cfg["level_definition"]["level_definition"][
                "map_"
            ][str(e.level.id)]["full_name_jp"]

        # level score, no real analog to rate
        res["rate"] = [0] * nplayers
        for e in record.head.accounts:
            res["rate"][
                e.seat
            ] = e.level.score  # level score, closest thing to rate

        # sex
        res["sx"] = ["C"] * nplayers

        # >names
        res["name"] = ["AI"] * nplayers
        for e in record.head.accounts:
            res["name"][e.seat] = e.nickname

        # scores
        scores = [
            [e.seat, e.part_point_1, e.total_point / 1000]
            for e in record.head.result.players
        ]
        res["sc"] = [0] * nplayers * 2
        for i, e in enumerate(scores):
            res["sc"][2 * e[0]] = e[1]
            res["sc"][2 * e[0] + 1] = e[2]

        # optional title - why not give the room and put the timestamp here
        res["title"] = [
            ruledisp + lobby,
            datetime.fromtimestamp(record.head.end_time).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        ]

        for item in record.data:
            self.handle(item)

            res["log"] = [kyoku.dump() for kyoku in self.kyokus]

        return res

    def handle(self, log: MjsLogItem):
        match log.name:
            case "RecordNewRound":
                self._handle_new_round(cast(RecordNewRound, log.data))
            case "RecordDiscardTile":
                self._handle_discard_tile(cast(RecordDiscardTile, log.data))
            case "RecordDealTile":
                self._handle_deal_tile(cast(RecordDealTile, log.data))
            case "RecordChiPengGang":
                self._handle_chi_peng_gang(cast(RecordChiPengGang, log.data))
            case "RecordAnGangAddGang":
                self._handle_an_gang_add_gang(
                    cast(RecordAnGangAddGang, log.data)
                )
            case "RecordBaBei":
                self._handle_ba_bei(cast(RecordBaBei, log.data))
            case "RecordLiuJu":
                self._handle_liu_ju(cast(RecordLiuJu, log.data))
            case "RecordNoTile":
                self._handle_no_tile(cast(RecordNoTile, log.data))
            case "RecordHule":
                self._handle_hu_le(cast(RecordHule, log.data))
            case _:
                raise RuntimeError(f"invalid log name: {log.name}")

    def _handle_new_round(self, log: RecordNewRound):
        self.cur = Kyoku(
            nplayers=len(log.scores),
            round=Round(4 * log.chang + log.ju, log.ben, log.liqibang),
            initscores=pad_list(list(log.scores), 4, 0),
            doras=(
                [Tile.parse(log.dora)]
                if log.dora
                else [Tile.parse(t) for t in log.doras]
            ),
            draws=[[] for i in range(4)],
            discards=[[] for i in range(4)],
            haipais=[
                [Tile.parse(t) for t in getattr(log, f"tiles{i}")]
                for i in range(4)
            ],
        )

        # 转换为庄家摸13张牌的形式
        self.poppedtile = self.cur.haipais[log.ju].pop()
        self.cur.draws[log.ju].append(self.poppedtile)

        # information we need, but can 't expect in every record
        self.dealerseat = log.ju

        # who dealt the last tile
        self.ldseat = -1
        # number of current riichis - needed for scores, abort workaround
        self.nriichi = 0

        self.priichi = False
        # number of current kans - only for abort workaround
        self.nkan = 0

        # 计算包牌
        self.nowinds = [
            0
        ] * self.cur.nplayers  # counter for each players open wind pons/kans
        self.nodrags = [0] * self.cur.nplayers
        self.paowind = (
            -1
        )  # seat of who dealt the final wind, -1 if no one is responsible
        self.paodrag = -1

    def _handle_discard_tile(self, log: RecordDiscardTile):
        if self.cur is None:
            raise RuntimeError("discard tile before new round")
        tile = Tile.parse(log.tile)

        tsumogiri = log.moqie
        # 特判庄家第一张的手摸切
        if (
            log.seat == self.dealerseat
            and len(self.cur.discards[log.seat]) == 0
            and tile == self.poppedtile
        ):
            tsumogiri = True

        sym = DiscardSymbol(tile, tsumogiri)

        # 立直宣言
        if log.is_liqi:
            self.priichi = True
            sym = DiscardSymbol(sym.tile, sym.tsumogiri, True)

        self.cur.discards[log.seat].append(sym)
        self.ldseat = log.seat

        # 更新dora
        if len(log.doras) > len(self.cur.doras):
            self.cur.doras = [Tile.parse(t) for t in log.doras]

    def _accept_riichi(self):
        if self.priichi:
            self.priichi = False
            self.nriichi += 1

    def _handle_deal_tile(self, log: RecordDealTile):
        if self.cur is None:
            raise RuntimeError("deal tile before new round")
        self._accept_riichi()

        # 更新dora
        if len(log.doras) > len(self.cur.doras):
            self.cur.doras = [Tile.parse(t) for t in log.doras]

        self.cur.draws[log.seat].append(Tile.parse(log.tile))

    def _countpao(self, tile: Tile, owner: int, feeder: int):
        if tile.type != TileType.Z:
            return

        if 1 <= tile.num <= 4:
            self.nowinds[owner] += 1
            if self.nowinds[owner] == 4:
                self.paowind = feeder
        elif 5 <= tile.num <= 7:
            self.nodrags[owner] += 1
            if self.nodrags[owner] == 4:
                self.paodrag = feeder

    def _handle_chi_peng_gang(self, log: RecordChiPengGang):
        if self.cur is None:
            raise RuntimeError("chi/peng/gang before new round")
        self._accept_riichi()

        if log.type == 0:
            # chi
            tiles = [Tile.parse(t) for t in log.tiles]
            self.cur.draws[log.seat].append(ChiSymbol(*tiles))
        elif log.type == 1:
            # pon
            tiles = [Tile.parse(t) for t in log.tiles]
            idx = relative_seating(log.seat, self.ldseat)
            self._countpao(tiles[0], log.seat, self.ldseat)
            self.cur.draws[log.seat].append(
                PonSymbol(tiles[0], tiles[1], tiles[2], idx)
            )
        elif log.type == 2:
            # daiminkan
            tiles = [Tile.parse(t) for t in log.tiles]
            idx = relative_seating(log.seat, self.ldseat)
            self._countpao(tiles[0], log.seat, self.ldseat)
            self.cur.draws[log.seat].append(
                DaiminkanSymbol(tiles[0], tiles[1], tiles[2], tiles[3], idx)
            )
            self.cur.discards[log.seat].append(
                ZeroSymbol()
            )  # tenhou drops a 0 in discards for this
            self.nkan += 1
        else:
            raise RuntimeError(f"invalid RecordChiPengGang.type={log.type}")

    def _handle_an_gang_add_gang(self, log: RecordAnGangAddGang):
        if self.cur is None:
            raise RuntimeError("an/kan before new round")
        # NOTE: e.tiles here is a single tile; naki is placed in discards
        tile = Tile.parse(log.tiles)
        self.ldseat = log.seat

        if log.type == 3:
            # ankan
            self._countpao(
                tile, log.seat, -1
            )  # count the group as visible, but don't set pao
            self.cur.discards[log.seat].append(AnkanSymbol(tile.deaka()))
            self.nkan += 1
        elif log.type == 2:
            # kakan
            # find pon and swap in new symbol
            for sy in self.cur.draws[log.seat]:
                if isinstance(sy, PonSymbol) and (
                    sy.tile == tile or sy.tile == tile.deaka()
                ):
                    self.cur.discards[log.seat].append(
                        KakanSymbol(
                            sy.a, sy.b, sy.tile, tile, sy.feeder_relative
                        )
                    )
                    self.nkan += 1
                    break
        else:
            raise RuntimeError(f"invalid RecordAnGangAddGang.type={log.type}")

    def _handle_ba_bei(self, log: RecordBaBei):
        if self.cur is None:
            raise RuntimeError("ba bei before new round")
        # kita - this record (only) gives {seat, moqie}
        self.cur.discards[log.seat].append(PeSymbol())

    def _handle_liu_ju(self, log: RecordLiuJu):
        if self.cur is None:
            raise RuntimeError("liu ju before new round")
        self._accept_riichi()

        if log.type == 1:
            self.cur.result = SpecialRyukyoku.kyushukyuhai
        elif log.type == 2:
            self.cur.result = SpecialRyukyoku.sufonrenda
        elif self.nriichi == 4:
            self.cur.result = SpecialRyukyoku.suuchariichi
        elif self.nkan == 4:
            self.cur.result = SpecialRyukyoku.suukaikan
        else:
            raise RuntimeError(f"invalid RecordLiuJu.type={log.type}")

        self.kyokus.append(self.cur)
        self.cur = None

    def _handle_no_tile(self, log: RecordNoTile):
        if self.cur is None:
            raise RuntimeError("no tile before new round")
        delta = [0, 0, 0, 0]

        # NOTE: mjs wll not give delta_scores if everyone is (no)ten
        # TODO: minimize the autism
        if (
            log.scores[0].delta_scores is not None
            and len(log.scores[0].delta_scores) != 0
        ):
            for score in log.scores:
                for i, g in enumerate(score.delta_scores):
                    # for the rare case of multiple nagashi, we sum the arrays
                    delta[i] += g

        self.cur.result = Ryukyoku(delta, getattr(log, "liujumanguan", False))

        self.kyokus.append(self.cur)
        self.cur = None

    def _tlround(self, x):
        """
        round up to nearest hundred iff TSUMOLOSSOFF == true otherwise return 0
        """
        if self.cur is None:
            raise RuntimeError("deal tile before new round")
        if self.tsumoloss_off:
            return 100 * ceil(x / 100)
        else:
            return 0

    def _parse_hu_le(self, hule: HuleInfo) -> SingleAgari:
        if self.cur is None:
            raise RuntimeError("deal tile before new round")

        # tenhou log viewer requires 点, 飜) or 役満) to end strings
        # rest of scoring string is entirely optional
        delta = (
            []
        )  # we need to compute the delta ourselves to handle double/triple ron
        points = None

        # riichi stick points, -1 means already taken
        if self.nriichi != -1:
            rp = 1000 * (self.nriichi + self.cur.round.riichi_sticks)
        else:
            rp = 0

        # base honba payment
        hb = 100 * self.cur.round.honba

        # sekinin barai logic
        pao = False
        liableseat = -1
        liablefor = 0

        if hule.yiman:
            # only worth checking yakuman hands
            for e in hule.fans:
                if e.id == DAISUUSHI and self.paowind != -1:
                    pao = True
                    liableseat = self.paowind
                    liablefor += e.val  # realistically can only be liable once
                elif e.id == DAISANGEN and self.paodrag != -1:
                    pao = True
                    liableseat = self.paodrag
                    liablefor += e.val  # realistically can only be liable once

        if hule.zimo:
            # ko-oya payment for non-dealer tsumo
            # delta  = [...new Array(kyoku.nplayers)].map(()=> (-hb - h.point_zimo_xian)); # noqa: E501
            delta = [
                -hb
                - hule.point_zimo_xian
                - self._tlround((1 / 2) * hule.point_zimo_xian)
            ] * self.cur.nplayers
            if hule.seat == self.dealerseat:  # oya tsumo
                delta[hule.seat] = (
                    rp
                    + (self.cur.nplayers - 1) * (hb + hule.point_zimo_xian)
                    + 2 * self._tlround(0.5 * hule.point_zimo_xian)
                )
                points = AgariPoint(
                    tsumo=hule.point_zimo_xian
                    + self._tlround((1 / 2) * hule.point_zimo_xian),
                    oya=True,
                )
            else:  # ko tsumo
                delta[hule.seat] = (
                    rp
                    + hb
                    + hule.point_zimo_qin
                    + (self.cur.nplayers - 2) * (hb + hule.point_zimo_xian)
                    + 2 * self._tlround((1 / 2) * hule.point_zimo_xian)
                )
                delta[self.dealerseat] = (
                    -hb
                    - hule.point_zimo_qin
                    - self._tlround((1 / 2) * hule.point_zimo_xian)
                )
                points = AgariPoint(
                    tsumo=hule.point_zimo_xian, tsumo_oya=hule.point_zimo_qin
                )
        else:
            delta = [0] * self.cur.nplayers
            delta[hule.seat] = (
                rp + (self.cur.nplayers - 1) * hb + hule.point_rong
            )
            delta[self.ldseat] = (
                -(self.cur.nplayers - 1) * hb - hule.point_rong
            )
            points = AgariPoint(ron=hule.point_rong, oya=hule.qinjia)
            self.nriichi = (
                -1
            )  # mark the sticks as taken, in case of double ron

        # sekinin barai payments
        # treat pao as the liable player paying back
        # the other players - safe for multiple yakuman

        if pao:
            # this is how tenhou does it
            # doesn't really seem to matter to akochan or tenhou.net/5

            if (
                hule.zimo
            ):  # liable player needs to payback n yakuman tsumo payments
                if hule.qinjia:  # dealer tsumo
                    # should treat tsumo loss as ron
                    # luckily all yakuman values round safely for
                    # north bisection
                    delta[liableseat] -= (
                        2 * hb
                        + liablefor * 2 * YSCORE[0][1]
                        + self._tlround(0.5 * liablefor * YSCORE[0][1])
                    )
                    for i, e in enumerate(delta):
                        if (
                            liableseat != i
                            and hule.seat != i
                            and self.cur.nplayers >= i
                        ):
                            delta[i] += (
                                hb
                                + liablefor * YSCORE[0][1]
                                + self._tlround(
                                    0.5 * liablefor * (YSCORE[0][1])
                                )
                            )
                    if (
                        self.cur.nplayers == 3
                    ):  # dealer should get north's payment from liable
                        delta[hule.seat] += (
                            liablefor * YSCORE[0][1]
                            if not self.tsumoloss_off
                            else 0
                        )
                else:  # non-dealer tsumo
                    delta[liableseat] -= (
                        (self.cur.nplayers - 2) * hb
                        + liablefor * (YSCORE[1][0] + YSCORE[1][1])
                        + self._tlround(0.5 * liablefor * YSCORE[1][1])
                    )  # ^^same 1st, but ko
                    for i, e in enumerate(delta):
                        if (
                            liableseat != i
                            and hule.seat != i
                            and self.cur.nplayers >= i
                        ):
                            if self.dealerseat == i:
                                delta[i] += (
                                    hb
                                    + liablefor * YSCORE[1][0]
                                    + self._tlround(
                                        0.5 * liablefor * YSCORE[1][1]
                                    )
                                )  # ^^same 1st
                            else:
                                delta[i] += (
                                    hb
                                    + liablefor * YSCORE[1][1]
                                    + self._tlround(
                                        0.5 * liablefor * YSCORE[1][1]
                                    )
                                )  # ^^same 1st
            # ron
            else:
                # liable seat pays the deal-in seat 1/2 yakuman + full honba
                # TODO: Type
                delta[liableseat] -= (  # type: ignore
                    self.cur.nplayers - 1
                ) * hb + 0.5 * liablefor * YSCORE[0 if hule.qinjia else 1][2]
                delta[self.ldseat] += (  # type: ignore
                    self.cur.nplayers - 1
                ) * hb + 0.5 * liablefor * YSCORE[0 if hule.qinjia else 1][2]

        return SingleAgari(
            seat=hule.seat,
            ldseat=hule.seat if hule.zimo else self.ldseat,
            paoseat=liableseat if pao else hule.seat,
            han=hule.count,
            fu=hule.fu,
            yaku=[Yaku(e.id, e.val) for e in hule.fans],
            oya=hule.qinjia,
            tsumo=hule.zimo,
            yakuman=hule.yiman,
            point=points,
            delta=delta,
        )

    def _handle_hu_le(self, log: RecordHule):
        if self.cur is None:
            raise RuntimeError("hu le before new round")
        agari = []
        ura = []

        # take the longest ura list - double ron with riichi + dama
        for f in log.hules:
            if f.li_doras is not None and len(ura) < len(f.li_doras):
                ura = [Tile.parse(t) for t in f.li_doras]
            agari.append(self._parse_hu_le(f))

        self.cur.result = Agari(agari=agari, uras=ura, round=self.cur.round)

        self.kyokus.append(self.cur)
        self.cur = None
