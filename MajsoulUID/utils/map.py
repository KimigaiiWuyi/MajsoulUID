import json
from typing import Dict
from pathlib import Path

lqc_path = Path(__file__).parent / 'proto' / 'lqc.json'
extend_res_path = Path(__file__).parent / 'proto' / 'extendRes.json'

with open(lqc_path, 'r', encoding='utf-8') as f:
    lqc: Dict = json.load(f)

with open(extend_res_path, 'r', encoding='utf-8') as f:
    extend_res: Dict[str, str] = json.load(f)
