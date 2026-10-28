import sys
import json
import types
import asyncio
import logging
import importlib
from pathlib import Path
from collections.abc import Callable, Generator

import httpx2
import pytest

ReviewFixture = tuple[types.ModuleType, dict[str, str]]


@pytest.fixture
def review(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Generator[ReviewFixture, None, None]:
    root = Path(__file__).resolve().parents[1] / "MajsoulUID"
    package = "_majsoul_review_tests"
    for suffix in ("", ".majs_notify", ".majs_notify.tenhou", ".majs_config", ".utils", ".utils.resource"):
        module = types.ModuleType(package + suffix)
        module.__path__ = [str(root.joinpath(*suffix.strip(".").split(".")))] if suffix else [str(root)]
        monkeypatch.setitem(sys.modules, package + suffix, module)
    config = {"MajsReviewEngine": "Tenhou", "MajsReviewToken": ""}
    config_module = types.ModuleType(f"{package}.majs_config.majs_config")
    config_module.MAJS_CONFIG = types.SimpleNamespace(get_config=lambda key: types.SimpleNamespace(data=config[key]))
    resource = types.ModuleType(f"{package}.utils.resource.RESOURCE_PATH")
    resource.PAIPU_PATH = tmp_path
    logger = types.ModuleType("gsuid_core.logger")
    logger.logger = logging.getLogger(__name__)
    for module in (config_module, resource, logger):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    name = f"{package}.majs_notify.tenhou.review"
    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delitem(sys.modules, f"{package}.majs_notify.paipu_cache", raising=False)
    module = importlib.import_module(name)
    yield module, config
    sys.modules.pop(name, None)
    sys.modules.pop(f"{package}.majs_notify.paipu_cache", None)


def mock_network(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> list[httpx2.AsyncClient]:
    client_type = httpx2.AsyncClient
    clients = []

    def client(**kwargs: object) -> httpx2.AsyncClient:
        instance = client_type(transport=httpx2.MockTransport(handler), **kwargs)
        clients.append(instance)
        return instance

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    return clients


@pytest.mark.parametrize("status_code", [200, 500])
def test_task_error_is_returned_without_caching(
    review: ReviewFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status_code: int
) -> None:
    module, _ = review

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/status":
            return httpx2.Response(200, json="ok")
        if request.method == "POST":
            return httpx2.Response(202, json={"task_id": "failed-task"})
        return httpx2.Response(
            status_code,
            json={"task_id": "failed-task", "status": "error", "error": "invalid chi: called tile", "data": None},
        )

    clients = mock_network(monkeypatch, handler)
    result = asyncio.run(module.review_tenhou({"game_id": "game"}))
    assert isinstance(result, str)
    assert "invalid chi: called tile" in result
    assert all(client.is_closed for client in clients)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("phase", ["post", "poll"])
def test_rate_limit_returns_message_and_closes_clients(
    review: ReviewFixture, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    module, _ = review

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/status":
            return httpx2.Response(200, json="ok")
        if request.method == "POST" and phase == "poll":
            return httpx2.Response(202, json={"task_id": "limited-task"})
        return httpx2.Response(429, text="Too many requests")

    clients = mock_network(monkeypatch, handler)
    result = asyncio.run(module.review_tenhou({"game_id": "game"}))
    assert isinstance(result, str) and "限流" in result
    assert all(client.is_closed for client in clients)


def test_cache_isolated_by_engine_and_player(
    review: ReviewFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module, config = review
    submissions = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/status":
            return httpx2.Response(200, json="ok")
        if request.method == "POST":
            submissions.append(json.loads(request.content))
            return httpx2.Response(202, json={"task_id": str(len(submissions))})
        return httpx2.Response(
            200, json={"task_id": str(len(submissions)), "status": "done", "error": None, "data": {"review": {}}}
        )

    clients = mock_network(monkeypatch, handler)
    (tmp_path / "game - review.json").write_text('{"status": "done", "task_id": "legacy"}')
    first = asyncio.run(module.review_tenhou({"game_id": "game", "_target_actor": 0}))
    again = asyncio.run(module.review_tenhou({"game_id": "game", "_target_actor": 0}))
    config["MajsReviewEngine"] = "Mjai"
    second = asyncio.run(module.review_tenhou({"game_id": "game", "_target_actor": 0}))
    third = asyncio.run(module.review_tenhou({"game_id": "game", "_target_actor": 2}))
    assert first["task_id"] == again["task_id"] == "1"
    assert second["task_id"] == "2"
    assert third["task_id"] == "3"
    assert [(payload["type"], payload["player_id"]) for payload in submissions] == [
        ("tenhou", 0),
        ("mjai", 0),
        ("mjai", 2),
    ]
    assert all(client.is_closed for client in clients)


def test_unstructured_http_failure_still_raises_and_closes_clients(
    review: ReviewFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, _ = review

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/status":
            return httpx2.Response(200, json="ok")
        if request.method == "POST":
            return httpx2.Response(202, json={"task_id": "proxy-failure"})
        return httpx2.Response(500, text="upstream failure")

    clients = mock_network(monkeypatch, handler)
    with pytest.raises(httpx2.HTTPStatusError):
        asyncio.run(module.review_tenhou({"game_id": "game"}))
    assert all(client.is_closed for client in clients)
