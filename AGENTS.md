# AGENTS.md

> 本文件遵循 [AGENTS.md](https://agents.md/)：给编码 Agent 的仓库说明（README for agents）。
> 人类用户说明见 [README.md](./README.md)。**源码是唯一事实源**。
>
> 账号池 / 推送 / review / proto：按需读
> [`.agents/skills/majsouluid-development/SKILL.md`](.agents/skills/majsouluid-development/SKILL.md)，
> **不要**一次把所有 `references/` 塞进上下文。

本仓库是 **GsCore 业务插件**，独立 git。放到 `gsuid_core/plugins/MajsoulUID/` 安装。

## Project overview

雀魂国服 / 日服 / 美服：战绩查询、好友牌谱推送、牌谱 Review、算符。

- 查询可走牌谱屋 HTTP（`utils/api`，Koromo）。实时推送需要账号池小号 WebSocket。
- `Plugins(name="MajsoulUID", force_prefix=["雀魂", "qh"], allow_empty_prefix=False)`。
- 绑定：**`MajsBind`**（不是 `GsBind`）。账号池：**`MajsUser`**（账密 / 日服 token）。
- **永远不要用大号登录**（命令文案必须保留警告）。
- **没有** `to_ai` / `@ai_tools`。
- Python `>=3.12`（`[project]`）。依赖 `betterproto`、`websockets`、`msgspec`、`httpx2`、`wasmtime`（牌谱屋 cap 的 hashwx PoW）。
- 版本：`MajsoulUID/version.py` 当前 `0.3.1`。

## Repository map

```
.
├── AGENTS.md / README.md / ICON.png
├── pyproject.toml / uv.lock            # 依赖锁只用 uv
├── ruff.toml                           # 照抄 Core 根 ruff.toml
├── __init__.py / __nest__.py
├── .agents/skills/majsouluid-development/
└── MajsoulUID/
    ├── __init__.py                     # 仅 Plugins(...)
    ├── __full__.py / version.py
    ├── majs_user/                      # 绑定、搜索
    ├── majs_info/                      # 查询战绩图
    ├── majs_notify/                    # WS 账号池、推送、review、好友
    │   └── tenhou/                     # Tenhou 日志解析
    ├── majs_fu/                        # 算符
    ├── majs_help/  majs_config/
    ├── lib/lq/                         # 雀魂 proto 生成代码（ruff.toml 排除 lint）
    ├── tools/                          # download_file.py
    └── utils/
        ├── api/                        # Koromo URL / request / remote；cap_solver + hashwx 求牌谱屋 cap token
        ├── database/models.py          # MajsBind / MajsUser / MajsPush / MajsPaipu
        ├── proto/                      # liqi.json / client_version.json / lqc.json + 更新脚本
        ├── texture2d/bighead/          # 皮肤头像（update_skin.py 生成）
        └── resource/RESOURCE_PATH.py
```

运行时：`{get_res_path()}/MajsoulUID/`（config、paipu）。

## Skills

| 任务 | 读 |
|------|-----|
| 本插件 | [majsouluid-development](.agents/skills/majsouluid-development/SKILL.md) |
| 补 `to_ai` | Core [gscore-plugin-development](../../../.agents/skills/gscore-plugin-development/SKILL.md) |
| 代码红线 | Core 根 [`AGENTS.md`](../../../AGENTS.md) §1–§4、§1.9 |

单独 clone 时打开宿主 Core 的 `AGENTS.md`。

## Setup commands

在**本插件目录**执行。解释器指向 Core 根 `.venv`。

```sh
uv run ruff check MajsoulUID tests
uv run ruff format --check MajsoulUID tests
# Core 的 .venv 没装 betterproto，跑测试时临时带上
uv run --no-project --python ../../../.venv/Scripts/python.exe --with "betterproto==2.0.0b7" python -m pytest tests -q
```

`ruff.toml` 照抄 Core 根的配置：**行宽 120**、lint `E/F/I/W/ASYNC`。`.pre-commit-config.yaml` 里 ruff 的版本和 Core uv dev 组锁的版本一致，升级时两边一起改。

**类型检查的坑**：在本目录直接跑 `basedpyright` 会读到 Core 根的 `pyrightconfig.json`，它排除了 `gsuid_core/plugins`，
结果是「0 errors」但其实一个文件都没查。要另写一份 `include` 本插件、`exclude` 掉 `MajsoulUID/lib/lq`、
`venvPath` 指 Core 根、`extraPaths` 加上 Core 根和本插件 `tests/`（测试之间互相 import）的配置，
用 `basedpyright --project <那份配置>` 跑。

## Code style

新代码与 Core 根 `AGENTS.md` **编号一致**，正反例以那份为准。

| 编号 | 要求 |
|------|------|
| §1.1 | 禁止 try-except 兜底。例外：牌谱 / 上游 JSON；登录网络错误转成用户可读文案 |
| §1.2–1.4 | 禁止 `cast` / 自身 `type: ignore` / `getattr`·`dict.get` 兜底 |
| §1.6 | `#` 最多两行、每行 ≤88 字 |
| §1.7 | 不改 Core `system_prompt` |
| §1.8 | 禁止 `Any` |
| §1.9 | 雀魂 / 和牌等垂直词只写本插件；不要写进框架意图表 |
| §2 | 函数全标注；PEP 604 |
| §3 | 无 `__tablename__`（可用 `__table_args__`）；`@with_session`；比较用 `col()`；`rowcount` 先 `isinstance(..., CursorResult)`。补列用 `exec_list.append('ALTER TABLE …')` |
| §4 | 全异步 |

密码 / token **禁止** `logger.info` 全文。历史代码脏则改到哪修到哪。

## Testing

`tests/` 离线跑，不连雀魂。连接层：`test_session.py` 测会话和 `RouteSocket`，`test_route_group.py` 测主 / 备用 / 探测线路；
假线路、假网关在 `tests/route_fakes.py`，`RouteSocket` 和切线路各有一个起本地 WebSocket 服务端的真连接测试。
改 `codec.py` / `route*.py` / `majsoul.py` 的登录重连 / `tenhou/parser.py` / `remote.py` 时补单测。
`config_default.py` import 时读 `core_config.masters`，脱离 Core 会炸，单测要 mock。

## 本仓库结构约定

- 嵌套加载：`__nest__.py` + `__full__.py`。
- `雀魂查询`：`get_uid(..., MajsBind)` → Koromo。文本含「三」「四」选人数，否则 `auto`。
- `雀魂搜索`：牌谱屋搜人，按钮引导绑定。
- 推送：登陆国服/日服/美服写入 `MajsUser` → Core 启动时自动登录（或 `推送启动`，`majs_notify/majsoul.py` 的 `manager`）→ `订阅` → `gs_subscribe`。
- 连接、登录、心跳、重连、被顶号的行为照官方客户端 Lua 来，细节和出处见 skill 的 `03-notify-review-and-proto.md`。
- `雀魂好友码`：列出账号池小号的好友码（`encode_account_id2(uid)`），群友在游戏里加 bot 好友用。好友码 ≠ UID。
- Review：`雀魂牌谱Review <url>`，引擎 `MajsReviewEngine` = Tenhou \| Mjai。缓存 `PAIPU_PATH`。
- 账号池命令 `pm=0`。不要手改 `lib/lq/` 生成物。

## 坑点

1. 大号登录有封号风险。
2. 无 WS 连接则 review / 好友不可用。
3. `MajsPaipu.insert_data` 在 `@with_session` 里调 `full_insert_data` 可能套会话。
4. 日/美服 token 在 `MajsUser.token`，Yostar passport uid 在 `MajsUser.account`（`uid` 是雀魂 account_id）。
5. 协议 / 皮肤数据来自 MajsoulData，别再从官网 `resversion*.json` 取（已停更）。

## Security notes

- `MajsUser` 账密、`MajsReviewToken` 禁止进 git、禁止 info 日志。
- 账号池 WS 出站连雀魂，不是 Core `/ws`。公网 Core 仍走 `WS_TOKEN`。
