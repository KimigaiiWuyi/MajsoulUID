import time
import asyncio
import importlib
from types import SimpleNamespace

import httpx2

PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"
request = importlib.import_module(f"{PKG}.utils.api.request")
remote_const = importlib.import_module(f"{PKG}.utils.api.remote_const")


def _api_returning(response: httpx2.Response):
    api = request.KoromoApi()
    api._client = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: response))
    return api


class _StubAuth:
    """离线替身：按次序吐 token，记录 rejected 参数。"""

    def __init__(self, *tokens: str) -> None:
        self._tokens = list(tokens)
        self.calls: list[str | None] = []

    async def get_token(self, rejected: str | None = None) -> str | None:
        self.calls.append(rejected)
        return self._tokens.pop(0) if self._tokens else None


def _records_api(monkeypatch, handler, *tokens: str):
    api = request.KoromoApi()
    stub = _StubAuth(*tokens)
    monkeypatch.setattr(request, "cap_auth", stub)
    api._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return api, stub


def test_extended_missing_fields_get_zero_defaults():
    api = _api_returning(httpx2.Response(200, json={"count": 3, "和牌率": 0.25}))
    extended = asyncio.run(api.get_player_extended(1))
    assert not isinstance(extended, int)
    assert extended["和牌率"] == 0.25
    assert extended["W立直"] == remote_const.player_extend_zero["W立直"]


def test_error_json_becomes_error_code():
    api = _api_returning(httpx2.Response(200, json={"error": "id_not_found"}))
    assert asyncio.run(api.get_player_stats(1)) == -1


def test_captcha_page_becomes_error_code():
    api = _api_returning(httpx2.Response(429, text="[x-cap-token-required] CAPTCHA required"))
    assert asyncio.run(api.get_player_stats(1)) == -999
    assert asyncio.run(api.search_player("a")) == []


def test_record_with_wrong_shape_is_rejected(monkeypatch):
    api, _ = _records_api(monkeypatch, lambda _: httpx2.Response(200, json=[{"uuid": "x"}]), "tok")
    assert asyncio.run(api.get_player_record(1)) == -1


def test_record_url_matches_official_shape(monkeypatch):
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["path"] = req.url.path
        seen["auth"] = req.headers.get("authorization")
        return httpx2.Response(200, json=[])

    api, _ = _records_api(monkeypatch, handler, "tok")
    asyncio.run(api.get_player_record(19900120))
    # 官方形状 /player_records/{pid}/{cursor=now}/{start}，见 records/loader.ts
    assert seen["path"].startswith("/api/v2/pl4/player_records/19900120/")
    assert seen["auth"] == "Bearer tok"


def test_record_rejects_bare_request_without_token(monkeypatch):
    api, stub = _records_api(monkeypatch, lambda _: httpx2.Response(429, text="[x-cap-token-required]"))
    assert asyncio.run(api.get_player_record(1)) == -999
    assert stub.calls == [None]


def test_record_refreshes_token_once_on_cap_429(monkeypatch):
    calls: list[dict] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        calls.append({"auth": req.headers.get("authorization"), "refreshed": "cap_token_refreshed" in str(req.url)})
        if req.headers.get("authorization") == "Bearer tok1":
            return httpx2.Response(429, text="[x-cap-token-required] CAPTCHA required")
        return httpx2.Response(200, json=[])

    api, stub = _records_api(monkeypatch, handler, "tok1", "tok2")
    assert asyncio.run(api.get_player_record(1)) == []
    assert calls[0] == {"auth": "Bearer tok1", "refreshed": False}
    assert calls[1] == {"auth": "Bearer tok2", "refreshed": True}
    assert stub.calls == [None, "tok1"]


def test_api_key_is_sent_as_bearer(monkeypatch):
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx2.Response(200, json={"count": 1})

    fake_config = SimpleNamespace(get_config=lambda key: SimpleNamespace(data="k3y"))
    monkeypatch.setattr(request, "MAJS_CONFIG", fake_config)
    api = request.KoromoApi()
    api._client = httpx2.AsyncClient(headers=dict(api._client.headers), transport=httpx2.MockTransport(handler))
    asyncio.run(api.get_player_stats(1))
    assert seen["auth"] == "Bearer k3y"


def test_no_key_means_no_authorization_header():
    seen = {}

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx2.Response(200, json={"count": 1})

    api = request.KoromoApi()
    api._client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    asyncio.run(api.get_player_stats(1))
    assert seen["auth"] is None


def test_burst_is_paced_to_one_qps():
    api = _api_returning(httpx2.Response(200, json={"count": 1}))
    start = time.monotonic()

    async def run() -> None:
        await asyncio.gather(*(api.get_player_stats(1) for _ in range(3)))

    asyncio.run(run())
    assert time.monotonic() - start >= 2 * request.KoromoApi._MIN_INTERVAL * 0.9
