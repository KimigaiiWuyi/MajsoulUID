# 三、推送、Review、proto

> 返回 [SKILL.md](../SKILL.md)

## 推送

`majs_notify/majsoul.py` 维持连接。观战/终局通过 `gs_subscribe.get_subscribe` 找到订阅者再 `target_send`。
任务名：`TASK_NAME_MAJSOUL_NOTIFY`、用户侧「雀魂观战订阅」。

好友状态推送在 `handle_FriendStateChange`：先更新好友状态再处理。对局结束后 `fetch_game_head` 用
`fetchGameRecordsDetail` 只查对局头部（名次、点数），按 `GAME_RECORD_RETRY_DELAYS` 重试；
同一局的几个好友共用一次查询。

牌谱接口有拉取限额，超了返回错误码 540「超出牌谱拉取限制」。所以：

- 牌谱类请求都走 `record_call`，串行且间隔 `RECORD_FETCH_INTERVAL`（牌谱屋爬虫也是 1 秒）
- 推送遇到 540 直接放弃，发牌谱链接，不再重试
- 推送不要调 `fetchGameRecord`（整局牌谱），只有 Review 的 `fetchLogs` 用它

错误码文案在 MajsoulData 的 `data/tables/info/error.json` → `raw/lua/.../Langs/info_error_chs.lua`，
常用的抄在 `constants.RECORD_ERROR_TEXT`。

维护：`MajsoulMaintenanceError`。推送对象还可按配置抄送给主人（`MajsIsPushActiveToMaster` + bot_id/type/id）。

## Review

1. 用户贴牌谱 URL，解析 query `paipu`。
2. 本地 `PAIPU_PATH/{id} - raw.json` 与 `review.json` 有缓存则直接画。
3. 否则从账号池连接 `fetchLogs`，对齐客户端 `GameMgr:CheckPaiPu`：`fetchGameRecord` 成功后补一句
   `readGameRecord`；`data` 为空时下载 `data_url`，相对路径拼上 `MajsoulRegion.record_prefix_url`。
   出错时返回错误文案（`str`）。
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
再用 protoc 生成 `lib/lq`，最后 `update_skin.py`（需要 `UnityPy`）。根目录 `ruff.toml` 和 `.pre-commit-config.yaml` 都跳过生成代码。

## 登录与连接

**改这一块之前先对照官方 Unity WebGL 客户端的 Lua，不要自己设计。** 数值都放在 `constants.py`，注释里写了出处。

| 文件 | 对应客户端 | 管什么 |
|------|-----------|--------|
| `codec.py` | `NetRoute._RequestToByte` / `_ParseMessageProto` | 编解码；序号 `(i+1)%60007` 全局共用；解不出的消息返回 `None` 跳过 |
| `route_delay.py` | `RouteDelayWatcher` / `NetworkQualityAnalyzer` | 算心跳里的 `delay`、`network_quality`；请求 10 秒超时 |
| `route.py` | `NetRoute` | 一条 WebSocket：`requestConnection` 带线路类型、每 15 秒（线路不稳 5 秒）路由心跳、测速连发 ping、备用 20 分钟 / 探测 40 秒自动关、`NotifyConnectionShutdown` 关路由 |
| `route_group.py` | `NetRouteGroup` + `GatewayFetcher` + `NetRouteGroup_Entrance` | 主 / 备用 / 探测三条线路、同线路重连、黑洞和重连超时切备用、线路表刷新 |
| `majsoul.py` 的 `MajsoulConnection` | `LobbyNetMgr` + `LoginMgr` + `UI_Entrance` | 登录、重连后 Relogin、被顶号、6 分钟挂机心跳、每小时 `fetchServerTime` |

- 网关：向各服入口网关请求 `/api/clientgate/routes`，再连路由的 `wss://<domain>/gateway`。WebSocket 带页面的
  `Origin` 和浏览器 UA，不发 ping 帧（浏览器不发），保活只靠 `Route.heartbeat`。
- 登录：token 先 `oauth2Check`（只发一次）再 `oauth2Login`；国服 token 失效时用存下的账密 `login`
  （插件自己的逻辑，官方是回登录页让人点）。账密登录的 `type`：纯数字当手机号（标签 1，str 3132「手机号码」），
  其余为 0（str 3127「邮箱/账号登录」）。密码原文入库，和官方 `SetPrefs('password', ...)` 一致。
- 错误码：`503` 封禁，直接失败；`156` 进排队（`fetchQueueInfo` 每 10 秒刷新，等 `NotifyLoginQueueFinished`）。
- 登录成功：`loginBeat`（失败隔 1 秒再发一次），有昵称再 `loginSuccess`，然后 `fetchInfo` 拿好友。
- 主线路选择：上次用的线路能用就用，否则在网关 `state` 为 idle / normal 的线路里挑 `level` 最高的随机一条。
- 断线：同一条路由按 `0.3/1/3/6/10/15` 秒重连，连上后 `oauth2Login(reconnect=true)`，再 `loginBeat` →
  `fetchServerTime` → `loginSuccess`，最后 `fetchInfo`（插件自己加的，要断线期间的好友状态）。
  6 次都连不上、或重登返回错误码，就放弃并推送提醒，等管理员 `重启服务`。
- 备用线路（每秒检查一次，`NeedOpenDetect()` 恒为 false）：
  - 没有备用就随机挑一条 idle、没被封禁的线路连上当备用（没连过的优先），连上后 `prepareLogin` 并测速。
  - 备用质量差于 0.25 秒时按冷却（3 秒～3 分钟）再开探测线路，比备用好 0.1 秒就 `requestRouteChange` 换成备用，
    否则封禁 3 或 10 分钟。探测前到点会刷新线路表（2 分钟一次）。
  - 主线路质量超过 4 秒持续 5 秒（黑洞），或重连超过 20 秒：备用线路好 0.5 秒以上就
    `requestRouteChange(type=Main)` + `fastLogin` 切过去，不再 Relogin。官方「只稍好一点」时弹按钮让人手动切，这里不切。
  - 插件自己的：重连中切备用前先停掉原线路的重连；切换失败或 `fastLogin` 失败就按断线处理（官方会卡在没有主线路）。
  - 只处理主线路上的通知。
- 被顶号（`NotifyAnotherLogin`）/ 服务端登出（`NotifyAccountLogout`）：关连接，自动重登一次；
  再来一次就停在 `KICKED`。官方是直接关自动登录，这里的「重登一次」是插件需求。
- 挂机心跳的 `no_operation_counter`：`IdleSimulator` 随机模拟动鼠标，永远不到 50 分钟（官方满 50 分钟会自己登出）。
- `loginBeat` 的 contract 在 `constants.LOGIN_BEAT_CONTRACT`，客户端升级后可能变。

### 拿官方 Lua

资源包里的 `.lua.bytes` 大多用 16 字节循环 XOR 加密，密钥 `wrelupqezdfrqdsd`（见 MajsoulData
`src/pipeline.py` 的 `LUA_XOR_KEY`）。资源清单在 `bundle_info_so.majset`，取法同 `update_skin.py`：
按资源路径（如 `LuaByte/Lua/UI/UI_AnotherLogin.lua.bytes`）找到所在资源包，UnityPy 读出 TextAsset 再解 XOR。

- 网络层：`Net/NetCore/NetRoute.lua`、`NetRouteGroup.lua`、`RouteDelayWatcher.lua`，登录在 `LoginMgr.lua`、
  `UI/UI_Entrance.lua`，顶号/登出/排队在 `UI/UI_AnotherLogin.lua`、`UI_Hanguplogout.lua`、`UI_LoginQueue.lua`。
- `Tools.StrOfLocalization(id)` 的文案：`Excels/Data/str/str_b*.lua` 里 `g[id]=b({id,...,下标})`，
  下标对 `Excels/Langs/str_str_chs.lua` 的字符串数组（从 1 数）。

不要在业务里 fork 字段名。
