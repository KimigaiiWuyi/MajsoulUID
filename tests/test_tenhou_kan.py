import pytest
from test_tenhou_chi import liblq, model, log_model, parser_mod


def make_parser(hand: tuple[str, ...] = ()) -> parser_mod.MajsoulPaipuParser:
    parser = parser_mod.MajsoulPaipuParser()
    parser.kyoku = model.Kyoku(
        nplayers=4,
        round=model.Round(0, 1, 0),
        initscores=[25000] * 4,
        doras=[model.Tile.parse("4s")],
        draws=[[], [], [], []],
        discards=[[], [], [], []],
        haipais=[[], [], [], [model.Tile.parse(tile) for tile in hand]],
        poppedtile=model.Tile.parse("1m"),
        dealerseat=0,
    )
    return parser


@pytest.mark.parametrize(
    ("tiles", "added", "feeder", "pon", "kakan", "drawn"),
    [
        (("1m", "1m", "1m"), "1m", 2, "p111111", "k11111111", 11),
        (("5m", "5m", "0m"), "5m", 2, "p511515", "k15511515", 15),
        (("5m", "5m", "0m"), "5m", 1, "15p5115", "15k155115", 15),
        (("5m", "5m", "0m"), "5m", 0, "1515p51", "1515k1551", 15),
        (("5m", "5m", "5m"), "0m", 2, "p151515", "k51151515", 51),
    ],
)
def test_kakan_preserves_pon_and_later_draw_order(
    tiles: tuple[str, str, str], added: str, feeder: int, pon: str, kakan: str, drawn: int
) -> None:
    parser = make_parser()
    for name, data in (
        ("RecordDiscardTile", liblq.RecordDiscardTile(seat=feeder, tile=tiles[2])),
        ("RecordChiPengGang", liblq.RecordChiPengGang(seat=3, type=1, tiles=list(tiles), froms=[3, 3, feeder])),
        ("RecordDiscardTile", liblq.RecordDiscardTile(seat=3, tile="9m")),
        ("RecordDealTile", liblq.RecordDealTile(seat=3, tile=added)),
        ("RecordAnGangAddGang", liblq.RecordAnGangAddGang(seat=3, type=2, tiles=added)),
        ("RecordDealTile", liblq.RecordDealTile(seat=3, tile="2p")),
        ("RecordDiscardTile", liblq.RecordDiscardTile(seat=3, tile="2p", moqie=True)),
    ):
        parser.handle(log_model.MjsLogItem(name=name, data=data))

    assert parser.kyoku.dump()[15] == [19, kakan, 60]
    assert parser.kyoku.dump()[14] == [pon, drawn, 22]
    assert parser.kyoku.nkan == 1


@pytest.mark.parametrize(
    ("hand", "drawn", "expected"),
    [
        (("5p", "5p", "5p"), "5p", "252525a25"),
        (("0p", "5p", "5p"), "5p", "522525a25"),
        (("0p", "0p", "5p"), "5p", "525225a25"),
        (("5p", "5p", "5p"), "0p", "252525a52"),
        (("7z", "7z", "7z"), "7z", "474747a47"),
    ],
)
def test_ankan_preserves_actual_tiles(hand: tuple[str, ...], drawn: str, expected: str) -> None:
    parser = make_parser(hand)
    parser.handle(log_model.MjsLogItem(name="RecordDealTile", data=liblq.RecordDealTile(seat=3, tile=drawn)))
    parser.handle(
        log_model.MjsLogItem(
            name="RecordAnGangAddGang",
            data=liblq.RecordAnGangAddGang(seat=3, type=3, tiles=drawn),
        )
    )

    assert parser.kyoku.dump()[15] == [expected]
    assert parser.kyoku.nkan == 1
