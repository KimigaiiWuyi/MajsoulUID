import asyncio
import importlib

import httpx2

PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"
request = importlib.import_module(f"{PKG}.utils.api.request")
remote_const = importlib.import_module(f"{PKG}.utils.api.remote_const")


def _api_returning(response: httpx2.Response):
    api = request.KoromoApi()
    api._client = httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: response))
    return api


def test_extended_missing_fields_get_zero_defaults():
    api = _api_returning(httpx2.Response(200, json={"count": 3, "和牌率": 0.25}))
    extended = asyncio.run(api.get_player_extended(1))
    assert not isinstance(extended, int)
    assert extended["和牌率"] == 0.25
    assert extended["W立直"] == remote_const.player_extend_zero["W立直"]


def test_error_json_becomes_error_code():
    api = _api_returning(httpx2.Response(200, json={"error": "id_not_found"}))
    assert asyncio.run(api.get_player_stats(1)) == -1
    assert asyncio.run(api.get_player_record(1)) == -1


def test_captcha_page_becomes_error_code():
    api = _api_returning(httpx2.Response(429, text="[x-cap-token-required] CAPTCHA required"))
    assert asyncio.run(api.get_player_record(1)) == -999
    assert asyncio.run(api.search_player("a")) == []


def test_record_with_wrong_shape_is_rejected():
    api = _api_returning(httpx2.Response(200, json=[{"uuid": "x"}]))
    assert asyncio.run(api.get_player_record(1)) == -1
