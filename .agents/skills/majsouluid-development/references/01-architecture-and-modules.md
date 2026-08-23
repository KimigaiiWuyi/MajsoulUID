# 一、架构与模块全景

> 返回 [SKILL.md](../SKILL.md)

```python
Plugins(name="MajsoulUID", force_prefix=["雀魂", "qh"], allow_empty_prefix=False)
```

| 路径 | 职责 |
|------|------|
| `majs_user/` | 绑定/切换/删除 UID，`搜索` 牌谱屋 |
| `majs_info/` | `查询` 战绩图（三/四麻） |
| `majs_notify/` | WS 账号池、订阅推送、好友、牌谱 Review、场况 |
| `majs_notify/tenhou/` | Tenhou 日志解析与 review |
| `majs_fu/` | `算符` 小游戏 |
| `majs_help/` / `majs_config/` | 帮助、配置 |
| `utils/api/` | Koromo HTTP（`api.py` URL、`request.py`、`models.py`） |
| `utils/api/remote.py` | 账号 ID 编码等 |
| `utils/database/models.py` | `MajsBind` `MajsUser` `MajsPush` `MajsPaipu` |
| `utils/proto/` `lib/lq/` | 雀魂协议 |
| `utils/resource/RESOURCE_PATH.py` | `MAIN_PATH` / `PAIPU_PATH` / `EXTEND_RES` |

两条数据面：

1. **无登录查询**：Koromo `5-data.amae-koromo.com`。
2. **登录能力**：`majsoul.py` 连接雀魂，拉牌谱、好友申请、观战通知。
