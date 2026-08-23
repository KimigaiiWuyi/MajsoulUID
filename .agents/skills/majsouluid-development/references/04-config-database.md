# 四、配置与数据库

> 返回 [SKILL.md](../SKILL.md)

## 配置

`MAJS_CONFIG` + `config_default.py`：

| 键 | 作用 |
|----|------|
| `UseFlowerHistory` | 战绩历史用花图 |
| `MajsIsPushActiveToMaster` | 订阅信息是否抄送推送对象 |
| `MajsFriendPushBotId` / `Type` / `ID` | 账号池元信息推送渠道 |
| `MajsIsAutoApplyFriend` | 自动同意好友 |
| `MajsReviewToken` | Mjai |
| `MajsReviewEngine` | Tenhou / Mjai |

`MajsFriendPushID` 默认取 Core `masters[0]` 或 `superusers[0]`。模块 import 依赖 `core_config`。

## 数据库

| 模型 | 基类 | 用途 |
|------|------|------|
| `MajsBind` | `Bind` | 用户↔雀魂 UID |
| `MajsUser` | `User` | 账号池（账密/token/lang/login_type） |
| `MajsPush` | `Push` | 推送开关 |
| `MajsPaipu` | `BaseIDModel` | 已拉牌谱 UUID |

WebConsole 均 `site.register_admin`。
`exec_list` 给 `MajsUser` 补 `username/password/account/token/lang/login_type` 列。

查询 UID：`get_uid(bot, ev, MajsBind)`（框架 `database.api`）。
