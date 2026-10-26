import json
import time
import asyncio
import importlib
from pathlib import Path
from dataclasses import dataclass

import pytest

PKG = "gsuid_core.plugins.MajsoulUID.MajsoulUID"
cap = importlib.import_module(f"{PKG}.utils.api.cap_solver")
hashwx = importlib.import_module(f"{PKG}.utils.api.hashwx")

# 牌谱屋线上抓的 blob；答案由已跑通 redeem 的上一版求解器算出
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "cap_instrumentation.json").read_text("utf-8"))


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["i"][:8])
def test_instrumentation_matches_recorded_answer(case):
    instr = cap._solve_instrumentation(case["blob"])
    assert instr.i == case["i"]
    assert instr.state == case["state"]


@dataclass
class _Node:
    parent: "_Node | None"
    text: int


def _dom_tree_walk(x: int, y: int, z: int) -> int:
    """照搬 blob 里 domHelper 的建树与回溯，作为 _dom_sum 的对照。"""
    node = _Node(None, 0)
    for v in (x, y, z):
        for _ in range(8):
            child = _Node(node, v)
            if v & 1 == 0:
                node = child
            v >>= 1
    s = 0
    while node.parent is not None:
        s += node.text
        node = node.parent
    return cap._js_mod(s, 256)


@pytest.mark.parametrize("xyz", [(0, 0, 0), (1, 2, 3), (-1, -2, 255), (2**31 - 1, -(2**31), 12345)])
def test_dom_sum_matches_tree_walk(xyz):
    assert cap._dom_sum(*xyz) == _dom_tree_walk(*xyz)


@pytest.mark.parametrize(
    ("c", "d", "n", "nonce"),
    [
        ("f21cbeca942017e04c72826c34928872f3066c67b54b3eda3cde2d1b576c57d1", 1000, 65536, 1309),
        # n 很小时解落在第 2 个 block，覆盖 seed 轮换
        ("612104f19e8b1592a02d2c92a36dd74cce541264f841ad6d6eb7f0e66f891af8", 50, 16, 19),
    ],
)
def test_hashwx_nonce_is_stable(c, d, n, nonce):
    assert asyncio.run(hashwx.solve_hashwx(c, d, n)) == nonce


def _auth_with_fake_fetch(monkeypatch, fail: bool = False):
    calls: list[int] = []

    async def fake_fetch():
        calls.append(1)
        await asyncio.sleep(0)
        if fail:
            raise cap.CapError("boom")
        return f"new{len(calls)}", time.time() + 7200

    monkeypatch.setattr(cap, "_fetch_token", fake_fetch)
    return cap.KoromoCapAuth(), calls


def test_token_near_expiry_is_reused_while_refreshing_in_background(monkeypatch):
    auth, calls = _auth_with_fake_fetch(monkeypatch)

    async def run():
        auth._token, auth._expires_at = "old", time.time() + 600
        assert await auth._get_token() == "old"
        await auth._background
        assert await auth._get_token() == "new1"

    asyncio.run(run())
    assert len(calls) == 1


def test_concurrent_rejections_refresh_once(monkeypatch):
    auth, calls = _auth_with_fake_fetch(monkeypatch)

    async def run():
        auth._token, auth._expires_at = "bad", time.time() + 7200
        return await asyncio.gather(*(auth._get_token(rejected="bad") for _ in range(3)))

    assert asyncio.run(run()) == ["new1"] * 3
    assert len(calls) == 1


def test_failed_refresh_sends_request_without_token(monkeypatch):
    auth, calls = _auth_with_fake_fetch(monkeypatch, fail=True)
    assert asyncio.run(auth._get_token()) is None
    assert len(calls) == 1
