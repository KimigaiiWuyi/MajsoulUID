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

`utils/proto/get_liqi.py`、`lqc.json`、`lib/lq/` 为协议生成物。
雀魂客户端升级导致登录/订阅失败时，先对照官方 liqi 再重新生成，不要在业务里 fork 字段名。
