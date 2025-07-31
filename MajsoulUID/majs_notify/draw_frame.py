import json
from pathlib import Path
from typing import Dict

from gsuid_core.utils.fonts.fonts import core_font as majs_font
from gsuid_core.utils.image.convert import convert_img
from gsuid_core.utils.image.image_tools import crop_center_img
from PIL import Image, ImageDraw

from ..utils.image import add_footer, get_bg
from .draw_review_info import PAI_PATH, draw_en_bg, kyoku_to_string
from .tenhou_meguru import MeguruLog, n2p

TEXT_LOG_PATH = Path(__file__).parent / "texture2d_log"
MASK = Image.open(TEXT_LOG_PATH / "mask.png")


def change_trans(image: Image.Image):
    pixels = image.load()
    if pixels is None:
        return image
    for i in range(image.width):
        for j in range(image.height):
            r, g, b, a = pixels[i, j]
            # 将透明度调整为60%
            new_a = int(a * 0.8)
            pixels[i, j] = (r, g, b, new_a)
    return image


async def render_frame(res: Dict, paipu: Dict, kyoku_id: int, meguru_id: int):
    if len(paipu["log"]) <= kyoku_id:
        return "该牌谱未存在该局, 请检查参数后重新输入！"

    real_paipu = paipu["log"][kyoku_id]
    w, h = 1400, 2000
    img = crop_center_img(get_bg(), w, h)
    title = Image.open(TEXT_LOG_PATH / "title.jpg").convert("RGBA")
    img.paste(title, (0, 0), title)

    divider = Image.open(TEXT_LOG_PATH / "divider.png")
    divider_draw = ImageDraw.Draw(divider)
    divider_str = kyoku_to_string(kyoku_id)
    divider_str += f"{real_paipu[0][1]}本场 第{meguru_id}巡"
    divider_draw.text(
        (700, 50),
        f"【{divider_str}】",
        "white",
        majs_font(38),
        "mm",
    )
    img.paste(divider, (0, 202), divider)

    frame = Image.open(TEXT_LOG_PATH / "frame.png")
    ml = MeguruLog(real_paipu, paipu["_target_actor"])
    megurus = ml.process()
    if meguru_id >= len(megurus):
        return "该牌谱该局未存在该巡, 请检查参数后重新输入！"

    mg: Dict[str, Dict[str, int]] = megurus[meguru_id]
    changed_mg = {}
    for player_id in mg:
        changed_mg[player_id] = {n2p(int(k)): mg[player_id][k] for k in mg[player_id]}

    with open("meguru.json", "w", encoding="utf-8") as f:
        json.dump(changed_mg, f, ensure_ascii=False, indent=4)

    wind = {
        "east": (500, 850),
        "south": (848, 850),
        "west": (848, 503),
        "north": (500, 503),
    }
    score = {
        (642, 805): real_paipu[1][0],
        (809, 653): real_paipu[1][1],
        (642, 567): real_paipu[1][2],
        (561, 652): real_paipu[1][3],
    }

    for pindex, player_id in enumerate(changed_mg):
        wind_pos = list(wind.values())[pindex]
        wind_name = list(wind.keys())[pindex]
        wind_card = Image.open(TEXT_LOG_PATH / f"{wind_name}.png")
        wind_card = wind_card.resize((60, 60))
        wind_card = wind_card.rotate(90 * pindex, expand=True)
        frame.paste(wind_card, wind_pos, wind_card)

        score_img = Image.new("RGBA", (140, 50))
        score_draw = ImageDraw.Draw(score_img)

        score_draw.text(
            (70, 25),
            f"{list(score.values())[pindex]}",
            (255, 146, 39),
            majs_font(30),
            "mm",
        )
        score_img = score_img.rotate(90 * pindex, expand=True)
        frame.paste(score_img, list(score.keys())[pindex], score_img)

        pais = changed_mg[player_id]
        for index, pai in enumerate(pais):
            is_mq = pais[pai]
            pai_img = Image.new("RGBA", (69, 110))
            _pai_img = Image.open(PAI_PATH / f"{pai}.png")
            _pai_img = _pai_img.resize((69, 110))

            pai_img.paste(_pai_img, (0, 0), MASK)

            if is_mq:
                pai_img = change_trans(pai_img)

            if pindex == 0:
                box = (495 + (index % 6) * 69, 902 + (index // 6) * 100)
            elif pindex == 1:
                box = (900 + (index // 6) * 100, 845 - (index % 6) * 69)
            elif pindex == 2:
                box = (839 - (index % 6) * 69, 398 - (index // 6) * 100)
            else:
                box = (395 - (index // 6) * 100, 505 + (index % 6) * 69)

            angle = 90 * pindex
            pai_img = pai_img.rotate(angle, expand=True)
            frame.paste(pai_img, box, pai_img)

    img.paste(frame, (0, 256), frame)

    _res = []
    for a in res["data"]["review"]["kyokus"][kyoku_id]["entries"]:
        if a["actual"]["type"] == "none":
            continue
        _res.append(a)
    en_bg, _ = draw_en_bg(_res[meguru_id], meguru_id, paipu["_target_actor"])
    img.paste(en_bg, (0, 1651), en_bg)

    img = add_footer(img)
    r = await convert_img(img)
    return r
