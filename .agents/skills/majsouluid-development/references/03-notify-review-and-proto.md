# 三、推送、Review、proto

> 返回 [SKILL.md](../SKILL.md)

## 推送

`majs_notify/majsoul.py` 维持连接。观战/终局通过 `gs_subscribe.get_subscribe` 找到订阅者再 `target_send`。
任务名：`TASK_NAME_MAJSOUL_NOTIFY`、用户侧「雀魂观战订阅」。

维护：`MajsoulMaintenanceError`。推送对象还可按配置抄送给主人（`MajsIsPushActiveToMaster` + bot_id/type/id）。

## Review

1. 用户贴牌谱 URL，解析 query `paipu`。
2. 本地 `PAIPU_PATH/{id} - raw.json` 与 `review.json` 有缓存则直接画。
3. 否则从账号池连接 `fetchLogs`。
4. 引擎：`MajsReviewEngine` Tenhou（`tenhou/review.py`）或 Mjai（需 `MajsReviewToken`）。
5. 出图：`draw_review_info.py`，牌面素材在 `texture2d_review/pai/`。

## proto

雀魂换成 Unity 客户端后，官网 `resversion*.json` 里的 `liqi.json` 停在 `v0.11.243.w`。
协议改从 [Avenshy/MajsoulData](https://github.com/Avenshy/MajsoulData) 的 Release 取 `liqi.desc`。

| 文件 | 由谁生成 | 用途 |
|------|---------|------|
| `lib/lq/__init__.py` | protoc + betterproto（`--descriptor_set_in=liqi.desc`） | 消息类 |
| `utils/proto/liqi.json` | `update_proto.py` | RPC 全名 → 请求/响应类型，`codec.py` 只认它 |
| `utils/proto/client_version.json` | `update_proto.py` | 登录上报的资源版本 / 客户端版本 |
| `utils/proto/lqc.json` | `update_skin.py` | 皮肤表（MajsoulData `item_definition/skin.json`） |
| `utils/texture2d/bighead/<皮肤ID>.webp` | `update_skin.py` | 好友排行榜头像，从国服 Unity 资源包解出 |

`.github/workflows/auto-liqi.yml` 每天跑一遍上面全部步骤。本地重跑：先 `update_proto.py --desc liqi.desc`，
再用 protoc 生成 `lib/lq`，最后 `update_skin.py`（需要 `UnityPy`）。`lib/ruff.toml` 让 ruff 跳过生成代码。

登录与连接对齐 Unity WebGL 客户端（Lua 源码在资源包 `LuaByte/Lua/` 下）：

- 网关：向 `constants.py` 里各服的入口网关请求 `/api/clientgate/routes`，再连路由的 `wss://<domain>/gateway`，
  连上后先发 `.lq.Route.requestConnection`。
- `client_version_string` 为 `WebGL_2022-<资源版本>`，`tag` / `currency_platforms` 按服区分。
- `loginBeat` 的 contract 在 `constants.LOGIN_BEAT_CONTRACT`，客户端升级后可能变。

不要在业务里 fork 字段名。
