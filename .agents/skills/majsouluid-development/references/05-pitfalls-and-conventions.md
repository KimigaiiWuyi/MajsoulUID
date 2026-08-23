# 五、坑点与规范

> 返回 [SKILL.md](../SKILL.md)

## 规范

新代码：Core `AGENTS.md` §1。本插件无 ruff.toml 时仍按 120 列写新文件。
不要把雀魂术语写进 Core 意图表。接 AI 用 `to_ai` + `covers`/`aliases` 前缀「雀魂·」。

## 坑

1. 大号登录封号。
2. 无 WS 连接则 review/好友不可用。
3. import `config_default` 会读 masters，脱离 Core 会失败。
4. 三/四麻靠 `ev.text` 含「三」「四」，其余 `auto`。
5. generated proto 不要手改。
6. 密码字段出现在 Admin 后台：新增日志必须脱敏。

## 改完自查

- [ ] 登录命令仍有「不要用大号」提示
- [ ] 绑定走 `MajsBind` 不是 `GsBind`
- [ ] 新表方法 `@with_session` + `col()`
- [ ] 牌谱缓存路径用 `PAIPU_PATH`
