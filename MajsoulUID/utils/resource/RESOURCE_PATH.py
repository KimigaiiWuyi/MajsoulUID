import sys

from gsuid_core.data_store import get_res_path

MAIN_PATH = get_res_path() / 'MajsoulUID'
sys.path.append(str(MAIN_PATH))

# 配置文件
CONFIG_PATH = MAIN_PATH / 'config.json'
