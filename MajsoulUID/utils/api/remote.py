import gettext

from .remote_const import (
    LEVEL_KONTEN,
    MODE_PENALTY,
    PLAYER_RANKS,
    LEVEL_MAX_POINTS,
    LEVEL_ALLOWED_MODES,
    PLAYER_RANKS_DETAIL,
    LEVEL_MAX_POINT_KONTEN,
)


def encode_account_id(account_id: int) -> int:
    return 1358437 + ((7 * account_id + 1117113) ^ 86216345)


def decode_account_id(id: int) -> int:
    return ((7 * id + 1117113) ^ 86216345) + 1358437


def decode_log_id(log_id):
    zero = ord("0")
    alpha = ord("a")

    ret = ""
    for i in range(len(log_id)):
        ch = log_id[i]
        code = ord(ch)

        if zero <= code < zero + 10:
            o = code - zero
        elif alpha <= code < alpha + 26:
            o = code - alpha + 10
        else:
            ret += ch
            continue

        o = (o + 55 - i) % 36
        if o < 10:
            ret += chr(o + zero)
        else:
            ret += chr(o + alpha - 10)

    return ret


def encode_account_id2(id: int) -> int:
    p = 6139246 ^ id
    H = 67108863
    s = p & ~H
    z = p & H
    for _ in range(5):
        z = ((511 & z) << 17) | (z >> 9)
    return z + s + 10000000


# 定义一个函数，返回一个列表，包含玩家等级的翻译
def getTranslatedLevelTags():
    # 使用gettext模块的gettext函数，根据当前语言环境，获取玩家等级的翻译
    rawTags = gettext.gettext(PLAYER_RANKS)
    # 如果翻译后的字符串的第一个字符的编码大于127，说明是非ASCII字符，例如中文
    if ord(rawTags[0]) > 127:
        # 则将字符串按照单个字符分割，返回一个列表
        return list(rawTags)
    # 否则，说明是ASCII字符，例如英文
    else:
        # 则将字符串按照两个字符为一组分割，返回一个列表
        rg = range(0, len(rawTags), 2)
        data = [rawTags[i : i + 2] for i in rg]  # noqa: E203
        return data


# 定义一个类，表示玩家的等级
class PlayerLevel:
    # 定义一个构造函数，接受一个整数参数，表示等级的编号
    def __init__(self, levelId: int, score: int = 0):
        self.id = levelId
        # 计算真实的编号，去掉前面的玩家编号
        realId: int = levelId % 10000
        self.score = score
        self.realId = realId
        # 计算主等级，即等级的前两位数字
        self._majorRank: int = realId // 100
        # 计算次等级，即等级的后两位数字
        self._minorRank: int = realId % 100
        # 计算玩家编号，即等级编号的前面部分
        self._numPlayerId: int = levelId // 10000

        self.major_rank = self.getFullTag()
        self.minor_rank = self.getMinorRank()
        self.full_tag = f"{self.major_rank}{self.minor_rank}"

        self.real_score = self.getVersionAdjustedScore(score)
        self.real_display_score = self.formatAdjustedScore(score)
        self.real_level_tag_with_score = self.formatAdjustedScoreWithTag(score)

    # 定义一个方法，返回等级的编号
    def toLevelId(self):
        return (
            self._numPlayerId * 10000 + self._majorRank * 100 + self._minorRank
        )

    def get_tag(self) -> str:
        label = PLAYER_RANKS[
            LEVEL_KONTEN - 2 if self.isKonten() else self._majorRank - 1
        ]
        if self.minor_rank == LEVEL_KONTEN - 1:
            return label
        if self.minor_rank == 1:
            return label + "一"
        elif self.minor_rank == 2:
            return label + "二"
        elif self.minor_rank == 3:
            return label + "三"
        else:
            raise ValueError(f"Unknown minor rank: {self.minor_rank}")

    # 定义一个方法，判断是否和另一个等级对象的主等级相同
    def isSameMajorRank(self, other):
        return self._majorRank == other._majorRank

    # 定义一个方法，判断是否和另一个等级对象完全相同
    def isSame(self, other: "PlayerLevel"):
        # 如果两个等级对象都是Konten等级，即最高等级
        if self.isKonten() and other.isKonten():
            # 如果其中一个等级对象的主等级是Konten等级的前一级，即第九级
            if (
                self._majorRank == LEVEL_KONTEN - 1
                or other._majorRank == LEVEL_KONTEN - 1
            ):
                # 则认为两个等级对象相同
                return True
            # 否则，比较两个等级对象的主等级和次等级是否都相同
            return (
                self._majorRank == other._majorRank
                and self._minorRank == other._minorRank
            )

    # 定义一个方法，判断是否允许某种游戏模式
    def isAllowedMode(self, mode):
        # 根据玩家编号和主等级，从一个常量列表中获取允许的游戏模式列表
        return (
            mode
            in LEVEL_ALLOWED_MODES[self._numPlayerId * 100 + self._majorRank]
        )

    # 定义一个方法，判断是否是Konten等级，即最高等级
    def isKonten(self):
        return self._majorRank >= LEVEL_KONTEN - 1

    # 定义一个方法，返回玩家编号
    def getNumPlayerId(self):
        return self._numPlayerId

    # 定义一个方法，返回一个新的等级对象，使用相同的玩家编号，但不同的等级编号
    def withLevelId(self, newLevelId):
        return PlayerLevel(self._numPlayerId * 10000 + newLevelId)

    # 定义一个方法，返回等级的标签，即等级的名称
    def getTag(self):
        # 从一个函数中获取等级的翻译列表
        label = getTranslatedLevelTags()[
            LEVEL_KONTEN - 2 if self.isKonten() else self._majorRank - 1
        ]
        # 如果主等级是Konten等级的前一级，即第九级
        if self._majorRank == LEVEL_KONTEN - 1:
            # 则只返回标签，不加次等级
            return label
        # 否则，返回标签加上次等级
        return label + str(self._minorRank)

    # 定义一个方法，返回等级的最大分数
    def getMaxPoint(self):
        # 如果是Konten等级，即最高等级
        if self.isKonten():
            # 如果次等级是20，即最高子等级
            if self._minorRank == 20:
                # 则返回0，表示没有上限
                return 0
            # 否则，返回一个常量，表示Konten等级的最大分数
            return LEVEL_MAX_POINT_KONTEN
        # 否则，根据主等级和次等级，从一个常量列表中获取对应的最大分数
        return LEVEL_MAX_POINTS[
            (self._majorRank - 1) * 3 + self._minorRank - 1
        ]

    # 定义一个方法，返回等级的惩罚分数，即失败时扣除的分数
    def getPenaltyPoint(self, mode):
        # 如果是Konten等级，即最高等级
        if self.isKonten():
            # 则返回0，表示没有惩罚
            return 0
        # 否则，根据游戏模式，主等级和次等级，从一个常量字典中获取对应的惩罚分数
        return MODE_PENALTY[mode][
            (self._majorRank - 1) * 3 + self._minorRank - 1
        ]

    # 定义一个方法，返回等级的起始分数，即升级到该等级时的分数
    def getStartingPoint(self):
        # 如果是第一级，即最低等级
        if self._majorRank == 1:
            # 则返回0，表示没有起始分数
            return 0
        # 否则，返回等级的最大分数的一半
        return self.getMaxPoint() / 2

    # 定义一个方法，返回等级的下一个等级对象
    def getNextLevel(self):
        # 获取调整后的等级对象，用于处理Konten等级的前一级的特殊情况
        level = self.getVersionAdjustedLevel()
        # 计算下一个等级的主等级
        majorRank = level._majorRank
        # 计算下一个等级的次等级
        minorRank = level._minorRank + 1
        # 如果次等级大于3，且不是Konten等级
        if minorRank > 3 and not level.isKonten():
            # 则主等级加一，次等级变为1
            majorRank += 1
            minorRank = 1
        # 如果主等级是Konten等级的前一级，即第九级
        if majorRank == LEVEL_KONTEN - 1:
            # 则主等级变为Konten等级，即第十级
            majorRank = LEVEL_KONTEN
        # 返回一个新的等级对象，使用相同的玩家编号，但不同的等级编号
        return PlayerLevel(
            level._numPlayerId * 10000 + majorRank * 100 + minorRank
        )

    # 定义一个方法，返回等级的上一个等级对象
    def getPreviousLevel(self):
        # 如果是第一级的第一子等级，即最低等级
        if self._majorRank == 1 and self._minorRank == 1:
            # 则返回自身，表示没有上一个等级
            return self
        # 获取调整后的等级对象，用于处理Konten等级的前一级的特殊情况
        level = self.getVersionAdjustedLevel()
        # 计算上一个等级的主等级
        majorRank = level._majorRank
        # 计算上一个等级的次等级
        minorRank = level._minorRank - 1
        # 如果次等级小于1
        if minorRank < 1:
            # 则主等级减一，次等级变为3
            majorRank -= 1
            minorRank = 3
        # 如果主等级是Konten等级的前一级，即第九级
        if majorRank == LEVEL_KONTEN - 1:
            # 则主等级变为Konten等级的前两级，即第八级
            majorRank = LEVEL_KONTEN - 2
        # 返回一个新的等级对象，使用相同的玩家编号，但不同的等级编号
        return PlayerLevel(
            level._numPlayerId * 10000 + majorRank * 100 + minorRank
        )

    # 定义一个方法，返回等级的调整后的等级对象，根据分数的变化
    def getAdjustedLevel(self, score):
        # 获取调整后的分数，用于处理Konten等级的前一级的特殊情况
        score = self.getVersionAdjustedScore(score)
        # 获取调整后的等级对象，用于处理Konten等级的前一级的特殊情况
        level = self.getVersionAdjustedLevel()
        # 获取等级的最大分数
        maxPoints = level.getMaxPoint()
        # 如果最大分数存在，且分数大于或等于最大分数
        if maxPoints and score >= maxPoints:
            # 则等级升为下一个等级
            level = level.getNextLevel()
            # 重新获取最大分数
            maxPoints = level.getMaxPoint()
            # 分数变为等级的起始分数
            score = level.getStartingPoint()
            # 否则，如果分数小于0
        elif score < 0:
            # 如果最大分数不存在，或等级是第一级，或等级是第二级的第一子等级
            if (
                not maxPoints
                or level._majorRank == 1
                or (level._majorRank == 2 and level._minorRank == 1)
            ):
                # 则分数变为0
                score = 0
            # 否则
            else:
                # 则等级降为上一个等级
                level = level.getPreviousLevel()
                # 重新获取最大分数
                maxPoints = level.getMaxPoint()
                # 分数变为等级的起始分数
                score = level.getStartingPoint()
        # 返回调整后的等级对象
        return level

    # 定义一个方法，返回调整后的等级对象，用于处理Konten等级的前一级的特殊情况
    def getVersionAdjustedLevel(self):
        # 如果主等级不是Konten等级的前一级，即第九级
        if self._majorRank != LEVEL_KONTEN - 1:
            # 则返回自身，表示没有调整
            return self
        # 否则，返回一个新的等级对象，使用相同的玩家编号，但主等级变为Konten等级，次等级变为1
        return PlayerLevel(self._numPlayerId * 10000 + LEVEL_KONTEN * 100 + 1)

    # 定义一个方法，返回调整后的分数，用于处理Konten等级的前一级的特殊情况
    def getVersionAdjustedScore(self, score):
        # 如果主等级是Konten等级的前一级，即第九级
        if self._majorRank == LEVEL_KONTEN - 1:
            # 则返回分数除以100，向上取整，再乘以10，再加上200
            return (score // 100) * 10 + 200
        # 否则，返回分数，表示没有调整
        return score

    # 定义一个方法，返回分数的显示格式
    def getScoreDisplay(self, score):
        # 获取调整后的分数，用于处理Konten等级的前一级的特殊情况
        score = self.getVersionAdjustedScore(score)
        # 如果是Konten等级，即最高等级
        if self.isKonten():
            # 则返回分数除以100，保留一位小数
            return f"{score / 100:.1f}"
        # 否则，返回分数的字符串形式
        return str(score)

    def GetMaxPointScoreDisplay(self) -> str:
        max_point = self.getMaxPoint()
        if self.isKonten():
            return f"{max_point / 100:.1f}"
        return str(max_point)

    # 定义一个方法，返回分数和等级标签的组合格式
    def formatAdjustedScoreWithTag(self, score):
        # 获取调整后的等级对象，根据分数的变化
        level = self.getAdjustedLevel(score)
        # 返回等级的标签和分数的格式的组合
        return f"{level.getTag()} {self.formatAdjustedScore(score)}"

    # 定义一个方法，返回分数的格式
    def formatAdjustedScore(self, score):
        score_display = f"{self.getScoreDisplay(score)}"
        if not self.getMaxPoint():
            max_point_display = ""
        else:
            max_point_display = f"/{self.GetMaxPointScoreDisplay()}"

        return f"{score_display}{max_point_display}"

    def getFullTag(self):
        return PLAYER_RANKS_DETAIL[
            LEVEL_KONTEN - 2 if self.isKonten() else self._majorRank - 1
        ]

    def getMinorRank(self):
        return self._minorRank
