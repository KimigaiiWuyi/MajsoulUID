import random
from pathlib import Path

from gsuid_core.utils.fonts.fonts import core_font as majs_font
from PIL import Image, ImageDraw, ImageFont


class MahjongScoring:
    def __init__(self):
        self.call_amount = 0
        self.last_tile_from = 0
        self.field_wind = ["東風圈", "南風圈"]
        self.self_wind = ["東家", "南家", "西家", "北家"]
        self.winning_style = ["自摸", "榮和"]
        self.field_seed = 0
        self.self_seed = 0
        self.field = ""
        self.self = ""
        self.win_by = ""
        self.taken_tiles = []
        self.hand = []
        self.shuntsu_amount = 0
        self.answer = []
        self.yaku_tile = []
        self.answer_in_number = 20
        self.winds = "zxcv"
        self.tiles = "123456789qwertyuioasdfghjklzxcvbnm"
        self.rotated_tiles = "!@#$%^&*(QWERTYUIOASDFGHJKLZXCVBNM"

        self.last_tile_str = ""
        self.last_tile_pos = -1

    async def draw_problem_image(self, include_answer=False) -> Image.Image:
        font = ImageFont.truetype(Path(__file__).parent / "mj.otf", 40)

        hand_str = "".join(self.hand)

        honor_num = 0
        for char in hand_str:
            if char not in "123456789qwertyuioasdfghjklzxcvbnm`":
                honor_num += 1

        # 计算图像大小
        tile_width = 30  # 每个麻将牌的宽度
        tile_height = 50  # 每个麻将牌的高度
        img_width = tile_width * len(hand_str) + honor_num * 10 + 10  # 图像宽度
        img_height = tile_height + 50 if not include_answer else tile_height + 120

        # 创建白色背景的图像
        image = Image.new("RGB", (img_width, img_height), "white")
        draw = ImageDraw.Draw(image)

        # 写上 风圈: 南風圈, 门风: 西家, 和牌方式: 榮和
        draw.text(
            xy=((5, 5)),
            text=f"风圈: {self.field}, 门风: {self.self}, 和牌方式: {self.win_by}",
            font=majs_font(24),
            align="m",
            fill="black",
        )

        cur_width = 5
        last_is_honor = False
        normal_tiles = set("123456789qwertyuioasdfghjklzxcvbnm`")
        tile_spacing = 30
        honor_spacing = 9

        # 绘制每个麻将牌
        for i, tile in enumerate(hand_str):
            if tile not in normal_tiles:
                if last_is_honor:
                    cur_width += honor_spacing
                    last_is_honor = False
                cur_width += tile_spacing if i != 0 else 0
                last_is_honor = True
            else:
                if last_is_honor:
                    cur_width += honor_spacing
                    last_is_honor = False
                cur_width += tile_spacing if i != 0 else 0
            if i == self.last_tile_pos:
                draw.text((cur_width, 50), self.last_tile_str, font=font, fill="red")
            else:
                draw.text((cur_width, 50), tile, font=font, fill="black")

        if include_answer:
            # 写上答案
            draw.text(
                xy=((10, 100)),
                text=self.answer[1],
                font=majs_font(12),
                fill="black",
            )
            draw.text(
                xy=((90, 100)),
                text=self.answer[2],
                font=majs_font(12),
                fill="black",
            )
            draw.text(
                xy=((170, 100)),
                text=self.answer[3],
                font=majs_font(12),
                fill="black",
            )
            draw.text(
                xy=((250, 100)),
                text=self.answer[4],
                font=majs_font(12),
                fill="black",
            )
            draw.text(
                xy=((330, 100)),
                text=self.answer[5],
                font=majs_font(12),
                fill="black",
            )
            draw.text(
                xy=((10, 135)),
                text=self.answer[0].replace("\n", " "),
                font=majs_font(12),
                fill="black",
            )
            draw.text(
                xy=((10, 150)),
                text=self.answer[6],
                font=majs_font(12),
                fill="black",
            )

        return image

    def generate_problem(self):
        self.call_amount = random.randint(0, 4)
        self.field_seed = random.randint(0, 1)
        self.self_seed = random.randint(0, 3)
        self.field = self.field_wind[self.field_seed]
        self.self = self.self_wind[self.self_seed]
        self.hand = []
        self.answer = []
        self.yaku_tile = (
            "bnm" + self.winds[self.field_seed] + self.winds[self.self_seed]
        )

        self.taken_tiles = [0] * 34

        # Pair
        t = random.randint(0, 33)
        self.taken_tiles[t] = 2
        self.hand.append(self.tiles[t] * 2)

        # Hand
        for i in range(1, 5):
            type_ = random.randint(0, 2)
            if type_ == 0:  # 順 (shuntsu)
                t = random.randint(0, 20)
                t = int(t / 7) * 9 + t % 7
                while (
                    self.taken_tiles[t] > 3
                    or self.taken_tiles[t + 1] > 3
                    or self.taken_tiles[t + 2] > 3
                ):
                    t = random.randint(0, 20)
                    t = int(t / 7) * 9 + t % 7
                self.taken_tiles[t] += 1
                self.taken_tiles[t + 1] += 1
                self.taken_tiles[t + 2] += 1
                self.hand.append(self.tiles[t] + self.tiles[t + 1] + self.tiles[t + 2])
                self.shuntsu_amount += 1
            elif type_ == 1:  # 碰 (pon)
                t = random.randint(0, 33)
                while self.taken_tiles[t] > 1:
                    t = random.randint(0, 33)
                self.taken_tiles[t] += 3
                self.hand.append(self.tiles[t] * 3)
            elif type_ == 2:  # 槓 (kan)
                t = random.randint(0, 33)
                while self.taken_tiles[t] > 0:
                    t = random.randint(0, 33)
                self.taken_tiles[t] += 4
                self.hand.append(self.tiles[t] * 4)
            else:
                print(f"Error: type = {type_}")

            # Move all kan to the end
            if i == 4:
                ptr = 4
                j = 1
                while j < ptr:
                    if len(self.hand[j]) == 4:
                        self.hand[j], self.hand[ptr] = (
                            self.hand[ptr],
                            self.hand[j],
                        )
                        j -= 1
                        ptr -= 1
                    j += 1

        self.last_tile_from = random.randint(0, 4 - self.call_amount)
        while len(self.hand[self.last_tile_from]) > 3:
            self.last_tile_from = random.randint(0, 4 - self.call_amount)
        self.last_tile = random.randint(0, len(self.hand[self.last_tile_from]) - 1)
        self.win_by = self.winning_style[random.randint(0, 1)]

    def calculate_score(self):
        # 平和 (Pinfu)
        if (
            self.call_amount == 0
            and self.shuntsu_amount >= 4
            and not any(tile in self.hand[0][0] for tile in "19qaol")
            and self.last_tile_from > 0
            and self.last_tile != 1
        ):
            self.answer = ["副底 +20", "-", "-", "-", "-", "-"]
            if self.win_by == "榮和":
                self.answer[0] += "\n門清榮和 +10"
                self.answer.append("<hr>平和榮和固定30符")
                self.answer_in_number = 30
            else:
                self.answer.append("<hr>平和自摸固定20符(不計自摸2符)")
                self.answer_in_number = 20

            i = self.last_tile_from
            if self.hand[i][2] not in "19qoal":
                self.last_tile_str = self.hand[i][0]
                for i in range(self.last_tile_from):
                    self.last_tile_pos += len(self.hand[i])
                self.last_tile_pos += 1
            else:
                self.last_tile_str = self.hand[i][2]
                for i in range(self.last_tile_from):
                    self.last_tile_pos += len(self.hand[i])
                self.last_tile_pos += 2
        else:
            # Common case
            self.answer = ["副底 +20", "", "", "", "", ""]
            self.answer_in_number = 20

            if self.win_by == "自摸":
                self.answer[0] += "\n自摸 +2"
                self.answer_in_number += 2

            if self.win_by == "榮和" and self.call_amount == 0:
                self.answer[0] += "\n門清榮和 +10"
                self.answer_in_number += 10

            # Check if the first tile is a Yaku or wind tile
            if self.hand[0][0] in "bnm":
                if self.answer[1] != "":
                    self.answer[1] += "\n"
                self.answer[1] += "役牌雀頭 +2"
                self.answer_in_number += 2
            if self.winds[self.field_seed] == self.hand[0][0]:
                if self.answer[1] != "":
                    self.answer[1] += "\n"
                self.answer[1] += "場風雀頭 +2"
                self.answer_in_number += 2
            if self.winds[self.self_seed] == self.hand[0][0]:
                if self.answer[1] != "":
                    self.answer[1] += "\n"
                self.answer[1] += "自風雀頭 +2"
                self.answer_in_number += 2

            if self.answer[1] == "":
                self.answer[1] = "一般雀頭 +0"

            # Processing melds
            for i in range(1, 5):
                meld = self.hand[i]
                if len(meld) == 4:  # Kong (Kan)
                    if self.answer[i + 1] != "":
                        self.answer[i + 1] += "\n"
                    if meld[0] in "1qa9olzxcvbnm":
                        if i > 4 - self.call_amount:
                            self.answer[i + 1] = "么九明槓 +16"
                            rand_num = int(random.random() * 3)
                            if rand_num > 0:
                                rand_num += 1
                            self.hand[i] = (
                                self.hand[i][:rand_num]
                                + self.rotated_tiles[
                                    self.tiles.index(self.hand[i][rand_num])
                                ]
                                + self.hand[i][rand_num + 1 :]
                            )
                            self.answer_in_number += 16
                        else:
                            self.answer[i + 1] = "么九暗槓 +32"
                            self.hand[i] = "`" + self.hand[i][1] + self.hand[i][2] + "`"
                            self.answer_in_number += 32
                    else:
                        if i > 4 - self.call_amount:
                            self.answer[i + 1] = "中張明槓 +8"
                            rand_num = int(random.random() * 3)
                            if rand_num > 0:
                                rand_num += 1
                            self.hand[i] = (
                                self.hand[i][:rand_num]
                                + self.rotated_tiles[
                                    self.tiles.index(self.hand[i][rand_num])
                                ]
                                + self.hand[i][rand_num + 1 :]
                            )
                            self.answer_in_number += 8
                        else:
                            self.answer[i + 1] = "中張暗槓 +16"
                            self.hand[i] = "`" + self.hand[i][1] + self.hand[i][2] + "`"
                            self.answer_in_number += 16
                elif meld[0] == meld[1]:  # Pon (Pung)
                    if meld[0] in "1qa9olzxcvbnm":  # 么九
                        if i > 4 - self.call_amount:
                            self.answer[i + 1] = "么九明刻 +4"
                            rand_num = int(random.random() * 3)
                            self.hand[i] = (
                                self.hand[i][:rand_num]
                                + self.rotated_tiles[
                                    self.tiles.index(self.hand[i][rand_num])
                                ]
                                + self.hand[i][rand_num + 1 :]
                            )
                            self.answer_in_number += 4
                        else:
                            self.answer[i + 1] = "么九暗刻 +8"
                            self.answer_in_number += 8
                    else:
                        if i > 4 - self.call_amount:
                            self.answer[i + 1] = "中張明刻 +2"
                            rand_num = int(random.random() * 3)
                            self.hand[i] = (
                                self.hand[i][:rand_num]
                                + self.rotated_tiles[
                                    self.tiles.index(self.hand[i][rand_num])
                                ]
                                + self.hand[i][rand_num + 1 :]
                            )
                            self.answer_in_number += 2
                        else:
                            self.answer[i + 1] = "中張暗刻 +4"
                            self.answer_in_number += 4
                else:  # Chow (Shuntsu)
                    self.answer[i + 1] = "順子 +0"
                    if i > 4 - self.call_amount:
                        self.hand[i] = (
                            self.rotated_tiles[self.tiles.index(self.hand[i][0])]
                            + self.hand[i][1]
                            + self.hand[i][2]
                        )
            if self.last_tile_from == 0:
                self.answer[1] += "\n聽單騎 +2"
                self.last_tile_str = self.hand[0][1]
                self.answer_in_number += 2
                self.last_tile_pos = 1
            else:
                i = self.last_tile_from
                if self.hand[i][0] == self.hand[i][1]:
                    if self.win_by == "榮和":
                        if self.hand[i][0] in "19qoalzxcvbnm":
                            self.answer_in_number -= 4
                            self.answer[i + 1] = "么九明刻 +4"
                        else:
                            self.answer_in_number -= 2
                            self.answer[i + 1] = "中張明刻 +2"
                    self.answer[i + 1] += "\n聽雙碰 +0"
                    self.last_tile_str = self.hand[i][2]
                    for j in range(i):
                        self.last_tile_pos += len(self.hand[j])
                    self.last_tile_pos += 3
                else:
                    rand_num = int(random.random() * 3)
                    if rand_num == 1:
                        self.answer[i + 1] += "\n聽嵌張 +2"
                        self.answer_in_number += 2
                        self.last_tile_str = self.hand[i][1]
                        for j in range(i):
                            self.last_tile_pos += len(self.hand[j])
                        self.last_tile_pos += 2
                    elif rand_num == 0:
                        if self.hand[i][2] in "19qoal":
                            self.answer[i + 1] += "\n聽邊張 +2"
                            self.answer_in_number += 2
                        else:
                            self.answer[i + 1] += "\n聽兩面 +0"
                        self.last_tile_str = self.hand[i][0]
                        for j in range(i):
                            self.last_tile_pos += len(self.hand[j])
                        self.last_tile_pos += 1
                    else:
                        if self.hand[i][0] in "19qoal":
                            self.answer[i + 1] += "\n聽邊張 +2"
                            self.answer_in_number += 2
                        else:
                            self.answer[i + 1] += "\n聽兩面 +0"
                        self.last_tile_str = self.hand[i][2]
                        for j in range(i):
                            self.last_tile_pos += len(self.hand[j])
                        self.last_tile_pos += 3

        # Final adjustments
        if len(self.answer) <= 6:
            self.answer.append(f"共{self.answer_in_number}符, 進位至十位數計")
        else:
            self.answer[6] = f"共{self.answer_in_number}符, 進位至十位數計"

        self.answer_in_number = (
            self.answer_in_number
            if self.answer_in_number % 10 == 0
            else (int(self.answer_in_number / 10) * 10 + 10)
        )
        self.answer[6] += f"{self.answer_in_number}符"

        if (
            self.call_amount > 0
            and self.shuntsu_amount >= 4
            and self.answer_in_number < 30
        ):
            self.answer[6] += "<br>非平和情況下, 若進位後仍不足30符, 以30符計算"
            self.answer_in_number = 30

    async def set_answer(self):
        self.calculate_score()
        print(f"风圈: {self.field}, 门风: {self.self}, 和牌方式: {self.win_by}")
        print(f"手牌: {self.hand}")
        print(f"答案: {self.answer}")
        print(f"分数: {self.answer_in_number}")
        return await self.draw_problem_image(include_answer=False)

    async def check_answer(self, answer: int):
        if answer == self.answer_in_number:
            return await self.draw_problem_image(include_answer=True)
        return False


# mahjong_scoring = MahjongScoring()
# mahjong_scoring.generate_problem()
# mahjong_scoring.set_answer()
