import pytest
from test_tenhou_chi import liblq, log_model, parser_mod


@pytest.mark.parametrize("nplayers", [3, 4])
def test_game_metadata_keeps_player_names_and_empty_sanma_seat(nplayers: int) -> None:
    record = log_model.MjsLog(
        head=liblq.RecordGame(
            uuid="metadata-test",
            accounts=[
                liblq.RecordGameAccountInfo(seat=1, nickname="Player", level=liblq.AccountLevel(id=10101, score=10))
            ],
            result=liblq.GameEndResult(
                players=[
                    liblq.GameEndResultPlayerItem(seat=seat, part_point_1=35000, total_point=0)
                    for seat in range(nplayers)
                ]
            ),
        ),
        data=[],
    )

    result = parser_mod.MajsoulPaipuParser().handle_game_record(record)

    assert result["ratingc"] == f"PF{nplayers}"
    assert result["name"] == ["AI", "Player", "AI", "" if nplayers == 3 else "AI"]
    assert result["sx"] == ["C", "C", "C", "" if nplayers == 3 else "C"]


@pytest.mark.parametrize(
    ("nplayers", "chang", "dealer", "seat", "seat_wind", "round_wind"),
    [
        (4, 0, 1, 1, "東", "東"),
        (4, 0, 1, 0, "北", "東"),
        (4, 1, 3, 0, "南", "南"),
        (3, 0, 1, 0, "西", "東"),
        (3, 1, 2, 0, "南", "南"),
    ],
)
def test_win_names_winds_relative_to_dealer(
    nplayers: int, chang: int, dealer: int, seat: int, seat_wind: str, round_wind: str
) -> None:
    parser = parser_mod.MajsoulPaipuParser()
    parser.handle(
        log_model.MjsLogItem(
            name="RecordNewRound",
            data=liblq.RecordNewRound(
                chang=chang,
                ju=dealer,
                scores=[35000] * nplayers,
                doras=["1p"],
                tiles0=["1p"],
                tiles1=["1p"],
                tiles2=["1p"],
                tiles3=["1p"] if nplayers == 4 else [],
            ),
        )
    )
    parser.handle(
        log_model.MjsLogItem(
            name="RecordHule",
            data=liblq.RecordHule(
                hules=[
                    liblq.HuleInfo(
                        seat=seat,
                        zimo=True,
                        qinjia=seat == dealer,
                        fans=[liblq.FanInfo(id=10, val=1), liblq.FanInfo(id=11, val=1)],
                        count=2,
                        fu=30,
                        point_zimo_xian=500,
                        point_zimo_qin=1000,
                    )
                ]
            ),
        )
    )

    win = parser.kyokus[0].dump()[-1][2]
    assert win[4:] == [f"自風 {seat_wind}(1飜)", f"場風 {round_wind}(1飜)"]
