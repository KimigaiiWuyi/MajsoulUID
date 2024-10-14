import asyncio

from gsuid_core.sv import SV
from gsuid_core.bot import Bot
from async_timeout import timeout
from gsuid_core.models import Event
from gsuid_core.logger import logger
from gsuid_core.utils.image.convert import convert_img

from .fu import MahjongScoring

sv_majs_fu = SV("雀魂算符小游戏")


@sv_majs_fu.on_fullmatch(("算符"))
async def send_help_img(bot: Bot, ev: Event):
    logger.info("开始执行[雀魂算符]")
    mahjong_scoring = MahjongScoring()
    mahjong_scoring.generate_problem()
    image = await mahjong_scoring.set_answer()

    await bot.send(
        "现在开始算符小游戏，你有60秒的时间回答问题，只需要回答数字即可，你可以发送取消来中止游戏。"
    )
    await bot.send(await convert_img(image))
    try:
        async with timeout(60):
            while True:
                resp = await bot.receive_mutiply_resp()
                if resp is not None:
                    if resp.text == "取消":
                        await bot.send("游戏已取消！")
                        return
                    if not resp.text.isdigit():
                        continue
                    result = await mahjong_scoring.check_answer(int(resp.text))
                    if not result:
                        await bot.send(
                            f"回答错误，正确符数为 {mahjong_scoring.answer_in_number}"
                        )
                        await bot.send(
                            await convert_img(
                                await mahjong_scoring.draw_problem_image(
                                    include_answer=True
                                )
                            )
                        )
                    else:
                        await bot.send("回答正确！")
                        await bot.send(await convert_img(result))
                    break
    except asyncio.TimeoutError:
        await bot.send("很遗憾，你没有在规定时间内回答问题。正确答案是：")
        await bot.send(
            await convert_img(
                await mahjong_scoring.draw_problem_image(include_answer=True)
            )
        )
