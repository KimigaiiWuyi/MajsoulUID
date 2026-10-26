from pathlib import Path

from msgspec import Struct, json

PROTO_PATH = Path(__file__).parent / "proto"
BIGHEAD_PATH = Path(__file__).parent / "texture2d" / "bighead"


class MajsoulSkin(Struct):
    id: int
    name: str
    character_id: int
    path: str | None


lqc = json.decode((PROTO_PATH / "lqc.json").read_bytes(), type=dict[str, MajsoulSkin])
