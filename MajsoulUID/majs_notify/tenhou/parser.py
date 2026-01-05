from math import ceil
from datetime import datetime
from typing import TypeVar, cast

from pydantic import Field, BaseModel

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

T = TypeVar("T")


class TenhouModel(BaseModel):
    ver: str = "2.3"
    ref: str = Field(default="")
    ratingc: str = Field(default="")
    rule: dict = Field(default_factory=dict)
    lobby: int = Field(default=0)
    dan: list[str] = Field(default_factory=list)
    rate: list[int] = Field(default_factory=list)
    sx: list[str] = Field(default_factory=list)
    name: list[str] = Field(default_factory=list)
    sc: list[float] = Field(default_factory=list)
    title: list[str] = Field(default_factory=list)
    log: list[list[dict]] = Field(default_factory=list)


class MajsoulPaipuParser:
    def __init__(self):
        self.kyokus: list[Kyoku] = []
        self.kyoku: Kyoku | None = None

    def handle_game_record(self, record: MjsLog):
        res = TenhouModel()
        ruledisp = ""
        lobby = ""  # usually 0, is the custom lobby number
        nplayers = len(record.head.result.players)
        nakas = nplayers - 1  # default

        # mlog version number
        res.ver = "2.3"
        # game id - copy and paste into "other" on the log page to view
        res.ref = record.head.uuid

        # PF4 is yonma, PF3 is sanma
        res.ratingc = f"PF{nplayers}"

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
            self.tsumoloss_off = (
                nplayers == 3
                and not record.head.config.mode.detail_rule.have_zimosun
            )
        elif record.head.config.meta.contest_uid:  # tourney
            lobby = f": {record.head.config.meta.contest_uid}"
            ruledisp += RUNES["tournament"][JPNAME]  # "Tournament"
            nakas = record.head.config.mode.detail_rule.dora_count
            self.tsumoloss_off = (
                nplayers == 3
                and not record.head.config.mode.detail_rule.have_zimosun
            )
        if record.head.config.mode.mode == 1:
            ruledisp += RUNES["tonpuu"][JPNAME]  # " East"
        elif record.head.config.mode.mode == 2:
            ruledisp += RUNES["hanchan"][JPNAME]

        if (
            record.head.config.meta.mode_id == 0
            and record.head.config.mode.detail_rule.dora_count == 0
        ):
            res.rule = {
                "disp": ruledisp,
                "aka53": 0,
                "aka52": 0,
                "aka51": 0,
            }
        else:
            res.rule = {
                "disp": ruledisp,
                "aka53": 1,
                "aka52": 2 if nakas == 4 else 1,
                "aka51": 1 if nplayers == 4 else 0,
            }

        # tenhou custom lobby
        # could be tourney id or friendly room for
        # mjs. appending to title instead to avoid 3->C etc. in tenhou.net/5
        res.lobby = 0

        # autism to fix logs with AI
        # ranks
        res.dan = [""] * nplayers
        for e in record.head.accounts:
            res.dan[e.seat] = cfg["level_definition"]["level_definition"][
                "map_"
            ][str(e.level.id)]["full_name_jp"]

        # level score, no real analog to rate
        res.rate = [0] * nplayers
        for e in record.head.accounts:
            res.rate[e.seat] = (
                e.level.score
            )  # level score, closest thing to rate

        # sex
        res.sx = ["C"] * nplayers

        # >names
        res.name = ["AI"] * nplayers
        for e in record.head.accounts:
            res.name[e.seat] = e.nickname

        # clean up for sanma AI
        if nplayers == 3:
            res.name[3] = ""
            res.sx[3] = ""

        # scores
        scores = [
            [e.seat, e.part_point_1, e.total_point / 1000]
            for e in record.head.result.players
        ]
        res.sc = [0.0] * nplayers * 2
        for i, e in enumerate(scores):
            res.sc[2 * e[0]] = e[1]
            res.sc[2 * e[0] + 1] = e[2]

        # optional title - why not give the room and put the timestamp here
        res.title = [
            ruledisp + lobby,
            datetime.fromtimestamp(record.head.end_time).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        ]

        for item in record.data:
            self.handle(item)

        res.log = [kyoku.dump() for kyoku in self.kyokus]

        return res.model_dump()

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
        nplayers = len(log.scores)
        self.kyoku = Kyoku(
            nplayers=nplayers,
            round=Round(4 * log.chang + log.ju, log.ben, log.liqibang),
            initscores=pad_list(list(log.scores), 4, 0),
            doras=(
                [Tile.parse(log.dora)]
                if log.dora
                else [Tile.parse(t) for t in log.doras]
            ),
            draws=[[], [], [], []],
            discards=[[], [], [], []],
            haipais=[
                [Tile.parse(t) for t in getattr(log, f"tiles{i}")]
                for i in range(nplayers)
            ],
            poppedtile=Tile(0, TileType.M),  # placeholder, will be set below
            # information we need, but can 't expect in every record
            dealerseat=log.ju,
        )

        self.kyoku.draws[log.ju].append(self.kyoku.haipais[log.ju].pop())

    def _handle_discard_tile(self, log: RecordDiscardTile):
        assert self.kyoku is not None, "discard tile before new round"
        tile = Tile.parse(log.tile)

        tsumogiri = log.moqie
        # 特判庄家第一张的手摸切
        if (
            log.seat == self.kyoku.dealerseat
            and len(self.kyoku.discards[log.seat]) == 0
            and tile == self.kyoku.poppedtile
        ):
            tsumogiri = True

        sym = DiscardSymbol(tile, tsumogiri)

        # 立直宣言
        if log.is_liqi:
            self.kyoku.priichi = True
            sym = DiscardSymbol(sym.tile, sym.tsumogiri, True)

        self.kyoku.discards[log.seat].append(sym)
        self.kyoku.ldseat = log.seat

        # 更新dora
        if len(log.doras) > len(self.kyoku.doras):
            self.kyoku.doras = [Tile.parse(t) for t in log.doras]

    def _accept_riichi(self):
        assert self.kyoku is not None, "accept riichi before new round"
        if self.kyoku.priichi:
            self.kyoku.priichi = False
            self.kyoku.nriichi += 1

    def _handle_deal_tile(self, log: RecordDealTile):
        assert self.kyoku is not None, "deal tile before new round"
        self._accept_riichi()

        # 更新dora
        if len(log.doras) > len(self.kyoku.doras):
            self.kyoku.doras = [Tile.parse(t) for t in log.doras]

        self.kyoku.draws[log.seat].append(Tile.parse(log.tile))

    def _handle_chi_peng_gang(self, log: RecordChiPengGang):
        assert self.kyoku is not None, "chi/peng/gang before new round"
        self._accept_riichi()

        if log.type == 0:
            # chii
            self.kyoku.draws[log.seat].append(
                ChiSymbol(
                    Tile.parse(log.tiles[2]),
                    Tile.parse(log.tiles[0]),
                    Tile.parse(log.tiles[1]),
                )
            )
        elif log.type == 1:
            # pon
            worktiles = [Tile.parse(t) for t in log.tiles]
            idx = relative_seating(log.seat, self.kyoku.ldseat)
            self.kyoku.countpao(worktiles[0], log.seat, self.kyoku.ldseat)
            # pop the called tile and prepend 'p'
            self.kyoku.draws[log.seat].append(
                PonSymbol(worktiles[0], worktiles[1], worktiles[2], idx)
            )
        elif log.type == 2:
            # daiminkan
            calltiles = [Tile.parse(t) for t in log.tiles]
            idx = relative_seating(log.seat, self.kyoku.ldseat)
            self.kyoku.countpao(calltiles[0], log.seat, self.kyoku.ldseat)
            self.kyoku.draws[log.seat].append(
                DaiminkanSymbol(
                    calltiles[0], calltiles[1], calltiles[2], calltiles[3], idx
                )
            )
            # tenhou drops a 0 in discards for this
            self.kyoku.discards[log.seat].append(ZeroSymbol())
            # register kan
            self.kyoku.nkan += 1
        else:
            raise RuntimeError(f"invalid RecordChiPengGang.type={log.type}")

    def _handle_an_gang_add_gang(self, log: RecordAnGangAddGang):
        assert self.kyoku is not None, "an/kan before new round"

        # NOTE: e.tiles here is a single tile; naki is placed in discards
        tile = Tile.parse(log.tiles)
        self.kyoku.ldseat = log.seat

        if log.type == 3:
            # ankan
            # mjs chun ankan example record:
            # {"seat":0,"type":3,"tiles":"7z"}

            # count the group as visible, but don't set pao
            self.kyoku.countpao(tile, log.seat, -1)

            # get the tiles from haipai and draws that
            # are involved in ankan, dumb
            # because n aka might be involved
            ankantiles = [
                t
                for t in self.kyoku.haipais[log.seat]
                if t.deaka() == tile.deaka()
            ] + [
                t
                for t in self.kyoku.draws[log.seat]
                if isinstance(t, Tile) and t.deaka() == tile.deaka()
            ]

            # doesn't really matter which tile we mark ankan with - choosing last drawn
            ankan_tile = ankantiles.pop() if ankantiles else tile

            self.kyoku.discards[log.seat].append(
                AnkanSymbol(ankan_tile.deaka())
            )
            self.kyoku.nkan += 1

        elif log.type == 2:
            # shouminkan
            # get pon naki from .draws and swap in new symbol
            for i, sy in enumerate(self.kyoku.draws[log.seat]):
                if isinstance(sy, PonSymbol) and (
                    sy.tile == tile or sy.tile == tile.deaka()
                ):
                    # remove the pon from draws and add kakan to discards
                    self.kyoku.draws[log.seat].pop(i)
                    self.kyoku.discards[log.seat].append(
                        KakanSymbol(
                            sy.a, sy.b, sy.tile, tile, sy.feeder_relative
                        )
                    )
                    self.kyoku.nkan += 1
                    break
        else:
            raise RuntimeError(f"invalid RecordAnGangAddGang.type={log.type}")

    def _handle_ba_bei(self, log: RecordBaBei):
        assert self.kyoku is not None, "ba bei before new round"
        # kita - this record (only) gives {seat, moqie}
        self.kyoku.discards[log.seat].append(PeSymbol())
        self.kyoku.ldseat = log.seat

    def _handle_liu_ju(self, log: RecordLiuJu):
        assert self.kyoku is not None, "liu ju before new round"
        self._accept_riichi()

        if log.type == 1:
            self.kyoku.result = SpecialRyukyoku.kyushukyuhai
        elif log.type == 2:
            self.kyoku.result = SpecialRyukyoku.sufonrenda
        elif self.kyoku.nriichi == 4:
            self.kyoku.result = SpecialRyukyoku.suuchariichi
        elif self.kyoku.nkan == 4:
            self.kyoku.result = SpecialRyukyoku.suukaikan
        else:
            raise RuntimeError(f"invalid RecordLiuJu.type={log.type}")

        self.kyokus.append(self.kyoku)
        self.kyoku = None

    def _handle_no_tile(self, log: RecordNoTile):
        assert self.kyoku is not None, "no tile before new round"
        delta = [0, 0, 0, 0]

        # NOTE: mjs wll not give delta_scores if everyone is (no)ten
        # TODO: minimize the autism
        if (
            log.scores
            and len(log.scores) > 0
            and log.scores[0].delta_scores is not None
            and len(log.scores[0].delta_scores) != 0
        ):
            for score in log.scores:
                for i, g in enumerate(score.delta_scores):
                    # for the rare case of multiple nagashi, we sum the arrays
                    delta[i] += g

        self.kyoku.result = Ryukyoku(
            delta, getattr(log, "liujumanguan", False)
        )

        self.kyokus.append(self.kyoku)
        self.kyoku = None

    def _tlround(self, x: float):
        """
        round up to nearest hundred iff TSUMOLOSSOFF == true otherwise return 0
        """
        if self.tsumoloss_off:
            return 100 * ceil(x / 100)
        else:
            return 0

    def _parse_hu_le(self, hule: HuleInfo, is_head_bump: bool) -> SingleAgari:
        assert self.kyoku is not None, "deal tile before new round"

        # tenhou log viewer requires 点, 飜) or 役満) to end strings
        # rest of scoring string is entirely optional
        # let res    = [h.seat, h.zimo ? h.seat : kyoku.ldseat, h.seat];
        delta = (
            []
        )  # we need to compute the delta ourselves to handle double/triple ron
        points = None

        # riichi stick points
        rp = (
            1000 * (self.kyoku.nriichi + self.kyoku.round.riichi_sticks)
            if is_head_bump
            else 0
        )

        # base honba payment
        hb = 100 * self.kyoku.round.honba if is_head_bump else 0

        # sekinin barai logic
        pao = False
        liableseat = -1
        liablefor = 0

        if hule.yiman:
            # only worth checking yakuman hands
            for e in hule.fans:
                if e.id == DAISUUSHI and self.kyoku.paowind != -1:
                    pao = True
                    liableseat = self.kyoku.paowind
                    liablefor += e.val  # realistically can only be liable once
                elif e.id == DAISANGEN and self.kyoku.paodrag != -1:
                    pao = True
                    liableseat = self.kyoku.paodrag
                    liablefor += e.val

        if hule.zimo:
            # ko-oya payment for non-dealer tsumo
            # delta  = [...new Array(kyoku.nplayers)].map(()=> (-hb - h.point_zimo_xian));
            tlround_part = self._tlround((1 / 2) * hule.point_zimo_xian)
            delta = [
                -hb - hule.point_zimo_xian - tlround_part
            ] * self.kyoku.nplayers
            if hule.seat == self.kyoku.dealerseat:  # oya tsumo
                delta[hule.seat] = (
                    rp
                    + (self.kyoku.nplayers - 1) * (hb + hule.point_zimo_xian)
                    + 2 * tlround_part
                )
                points = AgariPoint(
                    tsumo=hule.point_zimo_xian + tlround_part,
                    oya=True,
                )
            else:  # ko tsumo
                delta[hule.seat] = (
                    rp
                    + hb
                    + hule.point_zimo_qin
                    + (self.kyoku.nplayers - 2) * (hb + hule.point_zimo_xian)
                    + 2 * tlround_part
                )
                delta[self.kyoku.dealerseat] = (
                    -hb - hule.point_zimo_qin - tlround_part
                )
                points = AgariPoint(
                    tsumo=hule.point_zimo_xian, tsumo_oya=hule.point_zimo_qin
                )
        else:
            delta = [0] * self.kyoku.nplayers
            delta[hule.seat] = (
                rp + (self.kyoku.nplayers - 1) * hb + hule.point_rong
            )
            delta[self.kyoku.ldseat] = (
                -(self.kyoku.nplayers - 1) * hb - hule.point_rong
            )
            points = AgariPoint(ron=hule.point_rong, oya=hule.qinjia)

        # sekinin barai payments
        # treat pao as the liable player paying back
        # the other players - safe for multiple yakuman
        OYA = 0
        KO = 1
        RON = 2

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
                    tlround_part = self._tlround(
                        (1 / 2) * liablefor * YSCORE[OYA][KO]
                    )
                    delta[liableseat] -= (
                        2 * hb + liablefor * 2 * YSCORE[OYA][KO] + tlround_part
                    )
                    for i, e in enumerate(delta):
                        if (
                            liableseat != i
                            and hule.seat != i
                            and self.kyoku.nplayers >= i
                        ):
                            delta[i] += (
                                hb + liablefor * YSCORE[OYA][KO] + tlround_part
                            )
                    # dealer should get north's payment from liable
                    if self.kyoku.nplayers == 3:
                        delta[hule.seat] += (
                            0
                            if self.tsumoloss_off
                            else liablefor * YSCORE[OYA][KO]
                        )
                else:  # non-dealer tsumo
                    tlround_part = self._tlround(
                        (1 / 2) * liablefor * YSCORE[KO][KO]
                    )
                    delta[liableseat] -= (
                        (self.kyoku.nplayers - 2) * hb
                        + liablefor * (YSCORE[KO][OYA] + YSCORE[KO][KO])
                        + tlround_part
                    )  # ^^same 1st, but ko
                    for i, e in enumerate(delta):
                        if (
                            liableseat != i
                            and hule.seat != i
                            and self.kyoku.nplayers >= i
                        ):
                            if self.kyoku.dealerseat == i:
                                delta[i] += (
                                    hb
                                    + liablefor * YSCORE[KO][OYA]
                                    + tlround_part
                                )  # ^^same 1st
                            else:
                                delta[i] += (
                                    hb
                                    + liablefor * YSCORE[KO][KO]
                                    + tlround_part
                                )  # ^^same 1st
            # ron
            else:
                # liable seat pays the deal-in seat 1/2 yakuman + full honba
                points_ron = (
                    liablefor * YSCORE[OYA if hule.qinjia else KO][RON]
                )
                player_num = self.kyoku.nplayers - 1
                delta[liableseat] -= int(player_num * hb + 0.5 * points_ron)
                delta[self.kyoku.ldseat] += int(
                    player_num * hb + 0.5 * points_ron
                )

        return SingleAgari(
            seat=hule.seat,
            ldseat=hule.seat if hule.zimo else self.kyoku.ldseat,
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
        assert self.kyoku is not None, "hu le before new round"
        agari = []
        ura = []
        is_head_bump = True

        for f in log.hules:
            if f.li_doras is not None and len(ura) < len(f.li_doras):
                ura = [Tile.parse(t) for t in f.li_doras]
            agari.append(self._parse_hu_le(f, is_head_bump))
            is_head_bump = False  # subsequent rons don't get points

        self.kyoku.result = Agari(
            agari=agari, uras=ura, round=self.kyoku.round
        )
        self.kyokus.append(self.kyoku)
        self.kyoku = None
