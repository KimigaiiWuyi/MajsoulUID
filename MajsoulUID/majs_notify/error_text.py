"""雀魂错误码 → 官方中文提示。

表由 `utils/proto/update_proto.py` 从 MajsoulData 的 info/error 表和 `Langs/info_error_chs.lua` 生成。
"""

import msgspec

from ..utils.map import PROTO_PATH

ERROR_TEXT = msgspec.json.decode((PROTO_PATH / "error_text.json").read_bytes(), type=dict[int, str])


def error_message(code: int) -> str:
    text = ERROR_TEXT[code] if code in ERROR_TEXT else "未知错误"
    return f"{text}（错误码 {code}）"
