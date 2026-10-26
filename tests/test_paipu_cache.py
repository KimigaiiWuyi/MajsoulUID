import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def cache_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> types.ModuleType:
    root = Path(__file__).resolve().parents[1] / "MajsoulUID"
    package = "_majsoul_paipu_cache_tests"
    resource = types.ModuleType(f"{package}.utils.resource.RESOURCE_PATH")
    resource.PAIPU_PATH = tmp_path
    monkeypatch.setitem(sys.modules, resource.__name__, resource)
    spec = importlib.util.spec_from_file_location(
        f"{package}.majs_notify.paipu_cache", root / "majs_notify" / "paipu_cache.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", [None, 0])
def test_old_parser_cache_is_refetched(cache_module: types.ModuleType, tmp_path: Path, version: int | None) -> None:
    payload = {"game_id": "test-game", "log": ["old chi encoding"]}
    if version is not None:
        payload["_parser_version"] = version
    path = tmp_path / "test-game - raw.json"
    path.write_text(json.dumps(payload))

    assert asyncio.run(cache_module.get_paipu_by_game_id("test-game")) is None
    assert path.exists()


def test_current_parser_cache_is_reused(cache_module: types.ModuleType, tmp_path: Path) -> None:
    payload = {"game_id": "test-game", "_parser_version": cache_module.PAIPU_CACHE_VERSION, "log": []}
    (tmp_path / "test-game - raw.json").write_text(json.dumps(payload))

    assert asyncio.run(cache_module.get_paipu_by_game_id("test-game")) == payload


def test_missing_cache_returns_none(cache_module: types.ModuleType) -> None:
    assert asyncio.run(cache_module.get_paipu_by_game_id("missing-game")) is None
