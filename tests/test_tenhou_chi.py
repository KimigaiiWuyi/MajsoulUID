import sys
import types
import importlib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "MajsoulUID"
PKG = "_majsoul_tenhou_tests"

# Load the real parser without registering bot commands or starting GsCore.
for name, path in (
    (PKG, PLUGIN_ROOT),
    (f"{PKG}.majs_notify", PLUGIN_ROOT / "majs_notify"),
):
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    sys.modules[name] = package

parser_mod = importlib.import_module(f"{PKG}.majs_notify.tenhou.parser")
model = importlib.import_module(f"{PKG}.majs_notify.tenhou.model")
log_model = importlib.import_module(f"{PKG}.majs_notify.model")
liblq = importlib.import_module(f"{PKG}.lib.lq")


@pytest.mark.parametrize(
    ("tiles", "expected"),
    [
        (("2m", "3m", "1m"), "c111213"),
        (("1m", "3m", "2m"), "c121113"),
        (("1m", "2m", "3m"), "c131112"),
        (("0m", "7m", "6m"), "c165117"),
        (("4m", "6m", "0m"), "c511416"),
    ],
)
def test_chi_encodes_called_tile_before_consumed_tiles(
    tiles: tuple[str, str, str], expected: str
) -> None:
    parser = parser_mod.MajsoulPaipuParser()
    parser.kyoku = model.Kyoku(
        nplayers=4,
        round=model.Round(0, 1, 0),
        initscores=[25000] * 4,
        doras=[model.Tile.parse("4s")],
        draws=[[], [], [], []],
        discards=[[], [], [], []],
        haipais=[[], [], [], []],
        poppedtile=model.Tile.parse("1m"),
        dealerseat=0,
    )
    parser.handle(
        log_model.MjsLogItem(
            name="RecordDiscardTile",
            data=liblq.RecordDiscardTile(seat=2, tile=tiles[2]),
        )
    )
    parser.handle(
        log_model.MjsLogItem(
            name="RecordChiPengGang",
            data=liblq.RecordChiPengGang(
                seat=3, type=0, tiles=list(tiles), froms=[3, 3, 2]
            ),
        )
    )

    assert parser.kyoku.dump()[14] == [expected]
