from typing import Dict, List

from gsuid_core.config import core_config
from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsStrConfig,
    GsBoolConfig,
)

config_masters: List[str] = core_config.get_config('masters')
config_superusers: List[str] = core_config.get_config('superusers')
master: str = config_masters[0] if config_masters else ""
superuser: str = config_superusers[0] if config_superusers else ""

defalut_id = str(master if master else superuser)

CONFIG_DEFAULT: Dict[str, GSC] = {
    "UseFlowerHistory": GsBoolConfig(
        "对局历史使用花图替代折线图",
        "对局历史使用花图替代折线图",
        False,
    ),
    "MajsIsPushActiveToMaster": GsBoolConfig(
        "是否向推送对象也推送订阅信息",
        "设置后将会向下面设置的推送对象也推送订阅信息",
        False,
    ),
    "MajsFriendPushBotId": GsStrConfig(
        "账号池信息推送bot_id",
        "元信息的推送Bot",
        "onebot",
        ["onebot", "onebot_v12", "qq_group", "qq_guild"],
    ),
    "MajsFriendPushType": GsStrConfig(
        "账号池信息推送方式",
        "设置一些元信息的推送渠道",
        "direct",
        ["group", "direct"],
    ),
    "MajsFriendPushID": GsStrConfig(
        "账号池信息推送号码",
        "用于设置默认推送对象",
        defalut_id,
    ),
    "MajsIsAutoApplyFriend": GsBoolConfig(
        "账号池是否自动同意添加好友",
        "设置后账号池的账号将会自动同意添加所有好友申请",
        True,
    ),
    "MajsReviewToken": GsStrConfig(
        "神秘的Token", "没有？没关系，也能用。不够用？干碎小白！", ""
    ),
    "MajsReviewEngine": GsStrConfig(
        "Review引擎选择",
        "Tenhou: 小白甄选模型|Mjai: 高端模型(但是限流)",
        "Tenhou",
        ["Tenhou", "Mjai"],
    ),
    "MajsReviewForward": GsBoolConfig(
        "合并转发Review结果", "是否使用合并转发发送Review结果", False
    ),
}
