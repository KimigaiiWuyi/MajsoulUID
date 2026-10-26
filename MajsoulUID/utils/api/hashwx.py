"""hashwx PoW 求解。

hashwx.wasm 来自 tevador/hashwx@74b567a31276a4c5d7cb232a5b7639467f49f961
（LGPL-3，许可见同目录 hashwx.wasm.license.txt），与牌谱屋 cap 部署的二进制一致
（sha256 b1a0dbb3ef444d3c7069e0a5e0a0273ffa4cf8fef62cbbe43761c02f7cd6aff5）。
"""

import hashlib
import functools
from pathlib import Path
from collections.abc import Sequence

import wasmtime
from gsuid_core.pool import to_thread

_WASM_PATH = Path(__file__).parent / "hashwx.wasm"
_U64_MAX = (1 << 64) - 1
# 每个 block 含 n 个 nonce；上限远超期望值，只防协议异常时死循环
_MAX_BLOCKS = 512

# begin -> 本 block 生成的哈希函数 -> final -> 比目标，整个内循环留在 wasm 里，
# 省掉每个 nonce 约 30us 的跨语言调用
_DRIVER_WAT = """
(module
  (import "env" "memory" (memory 1))
  (import "env" "begin" (func $begin (param i32 i64)))
  (import "env" "final" (func $final (param i32) (result i64)))
  (import "env" "side" (func $side (param i32 i32)))
  (func (export "search") (param $ctx i32) (param $reg i32) (param $mem i32)
         (param $base i64) (param $count i32) (param $target i64) (result i64)
    (local $i i32) (local $nonce i64) (local $h i64)
    (block $done
      (loop $l
        (br_if $done (i32.ge_u (local.get $i) (local.get $count)))
        (local.set $nonce (i64.add (local.get $base) (i64.extend_i32_u (local.get $i))))
        (call $begin (local.get $ctx) (local.get $nonce))
        (call $side (local.get $reg) (local.get $mem))
        (local.set $h (call $final (local.get $ctx)))
        (if (i64.le_u (local.get $h) (local.get $target))
          (then (return (local.get $nonce))))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $l)))
    (i64.const -1))
)
"""


class HashwxError(Exception):
    """hashwx 求解失败。"""


@functools.cache
def _compiled() -> tuple[wasmtime.Engine, wasmtime.Module, wasmtime.Module]:
    engine = wasmtime.Engine()
    return (
        engine,
        wasmtime.Module.from_file(engine, _WASM_PATH),
        wasmtime.Module(engine, _DRIVER_WAT),
    )


class _Instance:
    """收窄 wasmtime 导出的类型：`exports[name]` 是六选一联合，`Func()` 返回 Any。"""

    def __init__(
        self,
        store: wasmtime.Store,
        module: wasmtime.Module,
        imports: Sequence[wasmtime.Func | wasmtime.Memory],
    ) -> None:
        self._store = store
        self._exports = wasmtime.Instance(store, module, imports).exports(
            store
        )

    def func(self, name: str) -> wasmtime.Func:
        export = self._exports[name]
        assert isinstance(export, wasmtime.Func), name
        return export

    def memory(self, name: str) -> wasmtime.Memory:
        export = self._exports[name]
        assert isinstance(export, wasmtime.Memory), name
        return export

    def call(self, name: str, *args: int) -> int:
        result = self.func(name)(self._store, *args)
        assert isinstance(result, int), name
        return result


# wasmtime 经 ctypes 调用，执行 wasm 时释放 GIL，多道题可以在线程池里并行跑
@to_thread
def solve_hashwx(c_hex: str, d: int, n: int) -> int:
    """返回满足难度的 nonce；实际哈希次数是 nonce + 1。

    判定与 cap 服务端 verifyHashwxSolution 一致：block = nonce // n，
    seed = sha256(c ‖ block 的 8 字节小端)，要求 hashwx(seed, nonce) <= U64_MAX // d。
    """
    engine, hashwx_module, driver_module = _compiled()
    store = wasmtime.Store(engine)
    hx = _Instance(store, hashwx_module, [])
    hx.func("_initialize")(store)
    ctx = hx.call("hashwx_alloc", 1)
    if ctx <= 0:
        raise HashwxError("hashwx compiled 模式不可用")

    memory = hx.memory("memory")
    seed_ptr = hx.call("hashwx_seed", ctx)
    reg_ptr = hx.call("hashwx_registers", ctx)
    mem_ptr = hx.call("hashwx_memory", ctx)
    challenge = bytes.fromhex(c_hex)
    target = _U64_MAX // d
    for block in range(_MAX_BLOCKS):
        memory.write(
            store,
            hashlib.sha256(challenge + block.to_bytes(8, "little")).digest(),
            seed_ptr,
        )
        hx.func("hashwx_make")(store, ctx, seed_ptr)
        # 每个 seed 会生成一段新的哈希函数 wasm，编译后接进 driver
        code_ptr = hx.call("hashwx_module", ctx)
        code = bytes(
            memory.read(
                store, code_ptr, code_ptr + hx.call("hashwx_module_size", ctx)
            )
        )
        side = _Instance(store, wasmtime.Module(engine, code), [memory]).func(
            "exec"
        )
        driver = _Instance(
            store,
            driver_module,
            [
                memory,
                hx.func("hashwx_exec_begin"),
                hx.func("hashwx_exec_final"),
                side,
            ],
        )
        nonce = driver.call(
            "search", ctx, reg_ptr, mem_ptr, block * n, n, target
        )
        if nonce >= 0:
            return nonce
    raise HashwxError(f"超过 {_MAX_BLOCKS} 个 block 仍未解出")
