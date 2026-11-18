"""不依赖 UnityPy，从雀魂的 Unity 资源包（UnityFS）里读少量数据。

只实现取版本号要用的部分：不压缩 / LZ4 的数据块、SerializedFile v22 起的对象表、
TextAsset，以及资源索引 `bundle_info_so.majset` 里的 BundleInfoSO。
格式对不上一律抛 UnityBundleError。
"""

import struct

CLASS_TEXT_ASSET = 49
CLASS_MONO_BEHAVIOUR = 114
# m_GameObject（PPtr 12 字节）+ m_Enabled（对齐到 4）+ m_Script（PPtr 12 字节），之后才是 m_Name
MONO_BEHAVIOUR_HEADER = 28
# SerializedFile v19 起每个类型树节点 32 字节（多了 m_RefTypeHash）
TYPE_TREE_NODE_SIZE = 32
MIN_SERIALIZED_VERSION = 22

COMPRESSION_NONE = 0
COMPRESSION_LZ4 = 2
COMPRESSION_LZ4HC = 3
FLAG_BLOCKS_INFO_AT_END = 0x80
FLAG_BLOCK_INFO_PADDING = 0x200


class UnityBundleError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes, pos: int = 0, endian: str = ">") -> None:
        self.data = data
        self.pos = pos
        self.endian = endian

    def take(self, size: int) -> bytes:
        if size < 0 or self.pos + size > len(self.data):
            raise UnityBundleError(f"数据在 {self.pos} 处截断，还要 {size} 字节")
        chunk = self.data[self.pos : self.pos + size]
        self.pos += size
        return chunk

    def _int(self, fmt: str) -> int:
        value = struct.unpack(self.endian + fmt, self.take(struct.calcsize(fmt)))[0]
        if not isinstance(value, int):
            raise UnityBundleError(f"{fmt} 读出来不是整数")
        return value

    def u8(self) -> int:
        return self._int("B")

    def i16(self) -> int:
        return self._int("h")

    def u16(self) -> int:
        return self._int("H")

    def i32(self) -> int:
        return self._int("i")

    def u32(self) -> int:
        return self._int("I")

    def i64(self) -> int:
        return self._int("q")

    def count(self) -> int:
        n = self.i32()
        if n < 0 or n > len(self.data) - self.pos:
            raise UnityBundleError(f"数组长度 {n} 不合理")
        return n

    def cstr(self) -> str:
        end = self.data.find(b"\0", self.pos)
        if end < 0:
            raise UnityBundleError("字符串没有结尾")
        text = self.data[self.pos : end].decode("utf-8", "replace")
        self.pos = end + 1
        return text

    def align(self, size: int) -> None:
        self.pos = (self.pos + size - 1) // size * size

    def aligned_str(self) -> str:
        text = self.take(self.count()).decode("utf-8", "replace")
        self.align(4)
        return text


def _lz4_extra_length(src: bytes, i: int, length: int) -> tuple[int, int]:
    while True:
        if i >= len(src):
            raise UnityBundleError("LZ4 长度字段截断")
        byte = src[i]
        i += 1
        length += byte
        if byte != 255:
            return length, i


def lz4_decompress(src: bytes, size: int) -> bytes:
    """LZ4 block 格式（不是 frame），Unity 的 LZ4 和 LZ4HC 块都是这种。"""
    dst = bytearray()
    i = 0
    while i < len(src):
        token = src[i]
        i += 1
        literal = token >> 4
        if literal == 15:
            literal, i = _lz4_extra_length(src, i, literal)
        if i + literal > len(src):
            raise UnityBundleError("LZ4 字面量截断")
        dst += src[i : i + literal]
        i += literal
        if i >= len(src):
            break
        if i + 2 > len(src):
            raise UnityBundleError("LZ4 偏移截断")
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        match = token & 15
        if match == 15:
            match, i = _lz4_extra_length(src, i, match)
        match += 4
        start = len(dst) - offset
        if offset == 0 or start < 0:
            raise UnityBundleError(f"LZ4 偏移 {offset} 越界")
        if offset >= match:
            dst += dst[start : start + match]
        else:
            # 重叠复制：要一个字节一个字节地抄，抄出来的字节会被后面接着用
            for k in range(match):
                dst.append(dst[start + k])
    if len(dst) != size:
        raise UnityBundleError(f"LZ4 解压后 {len(dst)} 字节，应为 {size}")
    return bytes(dst)


def _decompress(data: bytes, flags: int, size: int) -> bytes:
    kind = flags & 0x3F
    if kind == COMPRESSION_NONE:
        return data
    if kind in (COMPRESSION_LZ4, COMPRESSION_LZ4HC):
        return lz4_decompress(data, size)
    raise UnityBundleError(f"不支持的压缩方式 {kind}")


def bundle_files(raw: bytes) -> dict[str, bytes]:
    """UnityFS 资源包 → {包内文件名: 内容}。"""
    r = _Reader(raw)
    if r.cstr() != "UnityFS":
        raise UnityBundleError("不是 UnityFS 资源包")
    fmt = r.u32()
    r.cstr()
    r.cstr()
    r.i64()
    packed_size, info_size, flags = r.u32(), r.u32(), r.u32()
    if fmt >= 7:
        r.align(16)
    if flags & FLAG_BLOCKS_INFO_AT_END:
        packed = raw[len(raw) - packed_size :]
    else:
        packed = r.take(packed_size)
    info = _Reader(_decompress(packed, flags, info_size))
    info.take(16)
    blocks = [(info.u32(), info.u32(), info.u16()) for _ in range(info.count())]
    nodes: list[tuple[int, int, str]] = []
    for _ in range(info.count()):
        offset, size = info.i64(), info.i64()
        info.u32()
        nodes.append((offset, size, info.cstr()))
    if flags & FLAG_BLOCK_INFO_PADDING:
        r.align(16)
    data = b"".join(_decompress(r.take(packed), block_flags, size) for size, packed, block_flags in blocks)
    files: dict[str, bytes] = {}
    for offset, size, path in nodes:
        if offset < 0 or offset + size > len(data):
            raise UnityBundleError(f"包内文件 {path} 越界")
        files[path] = data[offset : offset + size]
    return files


def serialized_objects(sf: bytes) -> list[tuple[int, bytes]]:
    """SerializedFile → [(classID, 对象数据)]。只认 v22 起的格式（Unity 2020 起）。"""
    r = _Reader(sf)
    r.u32()
    r.u32()
    version = r.u32()
    r.u32()
    if version < MIN_SERIALIZED_VERSION:
        raise UnityBundleError(f"SerializedFile 版本 {version} 太旧")
    little = r.u8() == 0
    r.take(3)
    r.u32()
    r.i64()
    data_offset = r.i64()
    r.i64()
    r.endian = "<" if little else ">"
    r.cstr()
    r.i32()
    has_type_tree = r.u8() != 0
    class_ids: list[int] = []
    for _ in range(r.count()):
        class_id = r.i32()
        r.u8()
        r.i16()
        if class_id == CLASS_MONO_BEHAVIOUR:
            r.take(16)
        r.take(16)
        if has_type_tree:
            node_count, string_size = r.count(), r.count()
            r.take(node_count * TYPE_TREE_NODE_SIZE + string_size)
            r.take(4 * r.count())
        class_ids.append(class_id)
    objects: list[tuple[int, bytes]] = []
    for _ in range(r.count()):
        r.align(4)
        r.i64()
        start, size, type_index = r.i64(), r.u32(), r.i32()
        if not 0 <= type_index < len(class_ids):
            raise UnityBundleError(f"对象类型下标 {type_index} 越界")
        begin = data_offset + start
        if begin < 0 or begin + size > len(sf):
            raise UnityBundleError("对象数据越界")
        objects.append((class_ids[type_index], sf[begin : begin + size]))
    return objects


def _objects(raw: bytes) -> list[tuple[int, bytes]]:
    objects: list[tuple[int, bytes]] = []
    for path, content in bundle_files(raw).items():
        # .resS / .resource 是贴图音频的原始数据，不是 SerializedFile
        if not path.endswith((".resS", ".resource")):
            objects.extend(serialized_objects(content))
    return objects


def text_asset(raw: bytes, name: str) -> str:
    """资源包里名为 `name` 的 TextAsset 文本（m_Name 不带扩展名）。"""
    for class_id, data in _objects(raw):
        if class_id != CLASS_TEXT_ASSET:
            continue
        r = _Reader(data, endian="<")
        if r.aligned_str() == name:
            return r.aligned_str()
    raise UnityBundleError(f"资源包里没有 TextAsset {name}")


def parse_bundle_info(data: bytes, asset_path: str) -> str | None:
    """BundleInfoSO 对象数据里找 `asset_path` 所在的资源包文件名；不是 BundleInfoSO 返回 None。"""
    r = _Reader(data, MONO_BEHAVIOUR_HEADER, "<")
    if r.aligned_str() != "BundleInfoSO":
        return None
    names: list[str] = []
    for _ in range(r.count()):
        names.append(r.aligned_str())
        r.i32()
    for _ in range(r.count()):
        path, owner = r.aligned_str(), r.i32()
        if path != asset_path:
            continue
        if not 0 <= owner < len(names):
            raise UnityBundleError(f"{asset_path} 的资源包下标 {owner} 越界")
        return names[owner]
    raise UnityBundleError(f"资源索引里没有 {asset_path}")


def bundle_of_asset(index_raw: bytes, asset_path: str) -> str:
    """从 `bundle_info_so.majset` 查资源路径所在的资源包文件名。"""
    for class_id, data in _objects(index_raw):
        if class_id == CLASS_MONO_BEHAVIOUR:
            found = parse_bundle_info(data, asset_path)
            if found is not None:
                return found
    raise UnityBundleError("资源索引里没有 BundleInfoSO")
