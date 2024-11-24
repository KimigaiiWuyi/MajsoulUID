from gsuid_core.data_store import get_res_path

MAIN_PATH = get_res_path() / "MajsoulUID"

# 配置文件
CONFIG_PATH = MAIN_PATH / "config.json"

EXTEND_RES = MAIN_PATH / "extendRes"
CHARACTOR_PATH = EXTEND_RES / "charactor"
PAIPU_PATH = MAIN_PATH / "paipu"


for i in [EXTEND_RES, CHARACTOR_PATH, PAIPU_PATH]:
    if not i.exists():
        i.mkdir(parents=True)
