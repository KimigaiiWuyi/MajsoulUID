import json
from pathlib import Path

data_path = Path(__file__).parent / "data.json"
with open(data_path, "r", encoding="utf8") as f:
    cfg = json.load(f)

__all__ = ("cfg",)
