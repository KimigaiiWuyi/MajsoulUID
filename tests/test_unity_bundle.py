import struct
import importlib
from pathlib import Path

import pytest
from route_fakes import PKG

unity_bundle = importlib.import_module(f"{PKG}.utils.unity_bundle")
client_version = importlib.import_module(f"{PKG}.majs_notify.client_version")

# 国服 0.16.295 的 docs_version 资源包原样存档：UnityFS + LZ4HC + SerializedFile v22
DOCS_VERSION_BUNDLE = (Path(__file__).parent / "fixtures" / "docs_version.majset").read_bytes()


def aligned(text: str) -> bytes:
    raw = struct.pack("<i", len(text)) + text.encode()
    return raw + b"\0" * (-len(raw) % 4)


def test_docs_version_read_from_official_bundle():
    assert client_version.docs_version(DOCS_VERSION_BUNDLE) == "0.16.295"
    assert '"0.11.603"' in unity_bundle.text_asset(DOCS_VERSION_BUNDLE, "lua_version")
    with pytest.raises(unity_bundle.UnityBundleError):
        unity_bundle.text_asset(DOCS_VERSION_BUNDLE[:600], "version")


def test_bundle_info_maps_asset_to_bundle():
    def entries(*pairs: tuple[str, int]) -> bytes:
        return struct.pack("<i", len(pairs)) + b"".join(aligned(text) + struct.pack("<i", n) for text, n in pairs)

    data = b"\0" * unity_bundle.MONO_BEHAVIOUR_HEADER + aligned("BundleInfoSO")
    # bundleInfos[{name, fileSize}]，再是 assetInfos[{assetPath, ownerBundleIndex}]
    data += entries(("a_1.majset", 10), ("b_2.majset", 20))
    data += entries(("MyAssets/x.png", 0), ("MyAssets/docs_version/version.json", 1))
    assert unity_bundle.parse_bundle_info(data, "MyAssets/docs_version/version.json") == "b_2.majset"
    with pytest.raises(unity_bundle.UnityBundleError):
        unity_bundle.parse_bundle_info(data, "MyAssets/missing.json")


def test_lz4_overlapping_match():
    # 字面量 "ab"，再从 2 字节前抄 6 字节：抄的同时还在往后写
    assert unity_bundle.lz4_decompress(b"\x22ab\x02\x00", 8) == b"abababab"
    with pytest.raises(unity_bundle.UnityBundleError):
        unity_bundle.lz4_decompress(b"\x22ab\x09\x00", 8)
