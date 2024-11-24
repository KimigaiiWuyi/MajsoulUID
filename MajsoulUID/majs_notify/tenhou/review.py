import json
import time
import asyncio
from typing import Dict, List, Tuple, Union

import httpx
import aiofiles
from gsuid_core.logger import logger

from ...utils.resource.RESOURCE_PATH import PAIPU_PATH


async def check_url(tag: str, url: str):
    async with httpx.AsyncClient() as client:
        try:
            start_time = time.time()
            response = await client.get(f"{url}/status")
            elapsed_time = time.time() - start_time
            if response.status_code == 200:
                if response.json() == "ok":
                    logger.debug(f"{tag} {url} 延时: {elapsed_time}")
                    return tag, url, elapsed_time
                else:
                    logger.info(f"{tag} {url} 未超时但失效...")
                    return tag, url, float("inf")
            else:
                logger.info(f"{tag} {url} 超时...")
                return tag, url, float("inf")
        except httpx.ConnectError:
            logger.info(f"{tag} {url} 超时...")
            return tag, url, float("inf")


async def find_fastest_url(
    urls: Dict[str, str]
) -> List[Tuple[str, str, float]]:
    tasks = []
    for tag in urls:
        tasks.append(asyncio.create_task(check_url(tag, urls[tag])))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    return [
        result
        for result in results
        if not isinstance(result, (Exception, BaseException))
    ]


async def review_tenhou(tenhou_log: Dict[str, str]) -> Union[str, Dict]:
    game_id = tenhou_log["game_id"]
    path = PAIPU_PATH / f"{game_id} - review.json"
    if path.exists():
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            data = json.loads(await f.read())
            return data

    sess = httpx.AsyncClient(verify=False)
    urls = {
        "[wegt]": "https://majsoul.wget.es",
        "[cn]": "http://183.36.37.120:62800",
    }

    result = await find_fastest_url(urls)
    # Prefer [wegt] first if available
    tag, url = "", ""
    for result_tag, result_url, elapsed_time in result:
        if result_tag == "[wegt]" and elapsed_time != float("inf"):
            tag, url = result_tag, result_url
            break
    else:
        for result_tag, result_url, elapsed_time in result:
            if elapsed_time != float("inf"):
                tag, url = result_tag, result_url
                break
        else:
            return "❌ 未找到有效的Review接口!"

    logger.info(f"[Majsoul] Fastest Review URL: {tag} {url}")

    player_id = tenhou_log.get("_target_actor", 0)
    payload = {
        "type": "tenhou",
        "player_id": player_id,
        "data": tenhou_log,
    }

    response = await sess.post(f"{url}/review?type=Tenhou", json=payload)
    response.raise_for_status()
    task_id = response.json()["task_id"]

    for _ in range(15):
        response = await sess.get(f"{url}/review", params={"task": task_id})
        response.raise_for_status()
        res = response.json()
        status = res["status"]
        if status == "working":
            logger.info(f"[Majsoul] Review Task {task_id} is working...")
            await asyncio.sleep(3)
        elif status == "done":
            logger.info(f"[Majsoul] Review Task {task_id} is finished!")
            break
    else:
        return "❌ 未找到有效的Review信息!"

    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(res, ensure_ascii=False, indent=4))
    return res


async def get_review_result(res: dict):
    review_data = res["data"]["review"]
    rating: float = review_data["rating"] * 100
    matches_total = (
        review_data["total_matches"] / review_data["total_reviewed"]
    ) * 100
    bad_move_up_count = 0
    bad_move_down_count = 0

    for kyoku in review_data["kyokus"]:
        # cur_kyoku = kyoku["kyoku"]
        # cur_honba = kyoku["honba"]

        # print("--------------------")
        # print(f"Kyoku {cur_kyoku} Honba {cur_honba}")

        for entry in kyoku["entries"]:
            if entry["is_equal"]:
                continue

            actual = entry["actual"]

            for _, detail in enumerate(entry["details"]):
                if actual != detail["action"]:
                    continue
                if detail["prob"] <= 0.05:
                    bad_move_up_count += 1
                elif 0.05 < detail["prob"] <= 0.1:
                    bad_move_down_count += 1
                else:
                    continue

    bad_move_count = bad_move_up_count + bad_move_down_count

    Rating = f"{rating:.3f}"
    total_matches = f"{review_data['total_matches']}"
    total_reviewed: int = review_data["total_reviewed"]
    matches = f"{total_matches}/{total_reviewed}"
    total = f"{matches_total:.3f}%"

    bad_move_ratio = f"{bad_move_count}/{total_reviewed}"
    bad_move_percent = f"{(bad_move_count / total_reviewed) * 100:.3f}%"

    return (
        f"🥰 Review Info:\n"
        f"Rating: {Rating}\n"
        f"Matches/Total: {matches} = {total}\n"
        f"BadMove: {bad_move_count}\n"
        f"BadMoveRatio: {bad_move_ratio} = {bad_move_percent}"
    )
