from typing import Dict

from gsuid_core.handler import config_masters, config_superusers
from gsuid_core.utils.plugins_config.models import (
    GSC,
    GsStrConfig,
    GsBoolConfig,
)

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
}
