from typing import Union

UID_HINT = '[Majs] 你还没有绑定UID，请先使用[雀魂绑定]命令进行绑定'


error_dict = {
    -51: UID_HINT,
}


def get_error(retcode: Union[int, str]) -> str:
    return error_dict.get(int(retcode), f'未知错误, 错误码为{retcode}!')
