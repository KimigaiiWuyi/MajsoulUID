---
name: majsouluid-development
description: >
  当用户要求"维护/开发 MajsoulUID"、"雀魂插件"、"账号池登录"、"牌谱 review"、
  "雀魂推送启动"、"Koromo 牌谱屋"、"MajsBind / MajsUser"、"三麻四麻查询"、
  "Yostar 日服"、"改 MajsoulUID 有哪些坑"时触发此 SKILL。
  凡是改动 `gsuid_core/plugins/MajsoulUID` 的任务都应优先读取此 SKILL。
---

# MajsoulUID 插件开发与维护指南（核心入口）

> 源码是唯一事实源。按表打开**一篇** `references/`。

## 谁该读

| 任务 | 文档 |
|------|------|
| 改本插件 | **本 SKILL** |
| 补 `to_ai` | Core `gscore-plugin-development` §10 |
| 代码红线 | [`AGENTS.md`](../../../AGENTS.md) |

## 文档目录索引

| 章节 | 主题 | 链接 |
|------|------|------|
| 一 | 架构与模块 | [references/01-architecture-and-modules.md](./references/01-architecture-and-modules.md) |
| 二 | 命令与账号池 | [references/02-commands-and-account-pool.md](./references/02-commands-and-account-pool.md) |
| 三 | 推送、Review、proto | [references/03-notify-review-and-proto.md](./references/03-notify-review-and-proto.md) |
| 四 | 配置与数据库 | [references/04-config-database.md](./references/04-config-database.md) |
| 五 | 坑点与规范 | [references/05-pitfalls-and-conventions.md](./references/05-pitfalls-and-conventions.md) |

## 关键概念速记

- 前缀 `雀魂` / `qh`。绑定 `MajsBind`，不是 `GsBind`。
- 查询走 Koromo HTTP；推送走账号池 WebSocket。
- **禁止大号登录**。
- Review：Tenhou（默认）或 Mjai。
- 无 `to_ai`。
