from ..utils.api.remote_const import (
    LEVEL_KONTEN,
    LEVEL_MAX_POINT_KONTEN,
    LEVEL_MAX_POINTS,
    MODE_PENALTY,
    PLAYER_RANKS,
)


class MajsoulLevel:
    def __init__(self, levelId: int):
        real_id = levelId % 10000

        self.id = levelId
        self.major_rank = real_id // 100
        self.minor_rank = real_id % 100
        self.num_player_id = levelId // 10000

    def to_level_id(self):
        return self.num_player_id * 10000 + self.major_rank * 100 + self.minor_rank

    def is_konten(self) -> bool:
        return self.major_rank >= LEVEL_KONTEN - 1

    def get_tag(self) -> str:
        label = PLAYER_RANKS[
            LEVEL_KONTEN - 2 if self.is_konten() else self.major_rank - 1
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

    def is_same_major_rank(self, other: "MajsoulLevel"):
        return self.major_rank == other.major_rank

    def is_same(self, other: "MajsoulLevel"):
        if self.is_konten() and other.is_konten():
            if (
                self.major_rank == LEVEL_KONTEN - 1
                or other.major_rank == LEVEL_KONTEN - 1
            ):
                return True
            return (
                self.major_rank == other.major_rank
                and self.minor_rank == other.minor_rank
            )

    def get_max_point(self) -> int:
        if self.is_konten():
            if self.minor_rank == 20:
                return 0
            return LEVEL_MAX_POINT_KONTEN
        return LEVEL_MAX_POINTS[(self.major_rank - 1) * 3 + self.minor_rank - 1]

    def getPenaltyPoint(self, mode):
        if self.is_konten():
            return 0
        return MODE_PENALTY[mode][(self.major_rank - 1) * 3 + self.minor_rank - 1]

    def get_starting_point(self) -> int:
        if self.major_rank == 1:
            return 0
        return self.get_max_point() // 2

    def get_next_level(self):
        level = self.get_version_adjusted_level()
        majorRank = level.major_rank
        minorRank = level.minor_rank + 1
        if minorRank > 3 and not level.is_konten():
            majorRank += 1
            minorRank = 1
        if majorRank == LEVEL_KONTEN - 1:
            majorRank = LEVEL_KONTEN
        return MajsoulLevel(level.num_player_id * 10000 + majorRank * 100 + minorRank)

    def get_previous_level(self):
        if self.major_rank == 1 and self.minor_rank == 1:
            return self
        level = self.get_version_adjusted_level()
        majorRank = level.major_rank
        minorRank = level.minor_rank - 1
        if minorRank < 1:
            majorRank -= 1
            minorRank = 3
        if majorRank == LEVEL_KONTEN - 1:
            majorRank = LEVEL_KONTEN - 2
        return MajsoulLevel(level.num_player_id * 10000 + majorRank * 100 + minorRank)

    def get_adjusted_level(self, score: int):
        score = self.get_version_adjusted_score(score)
        level = self.get_version_adjusted_level()
        maxPoints = level.get_max_point()
        if maxPoints and score >= maxPoints:
            level = level.get_next_level()
            maxPoints = level.get_max_point()
            score = level.get_starting_point()
        elif score < 0:
            if (
                not maxPoints
                or level.major_rank == 1
                or (level.major_rank == 2 and level.minor_rank == 1)
            ):
                score = 0
            else:
                level = level.get_previous_level()
                maxPoints = level.get_max_point()
                score = level.get_starting_point()
        return level

    def get_version_adjusted_score(self, score: int) -> int:
        if self.major_rank == LEVEL_KONTEN - 1:
            return (score // 100) * 10 + 200
        return score

    def get_version_adjusted_level(self):
        if self.major_rank != LEVEL_KONTEN - 1:
            return self
        return MajsoulLevel(self.num_player_id * 10000 + LEVEL_KONTEN * 100 + 1)

    def get_score_display(self, score: int) -> str:
        score = self.get_version_adjusted_score(score)
        if self.is_konten():
            return f"{score / 100:.1f}"
        return str(score)

    def get_max_point_score_display(self) -> str:
        max_point = self.get_max_point()
        if self.is_konten():
            return f"{max_point / 100:.1f}"
        return str(max_point)

    def formatAdjustedScoreWithTag(self, score: int) -> str:
        level = self.get_adjusted_level(score)
        return f"{level.get_tag()} {self.formatAdjustedScore(score)}"

    def formatAdjustedScore(self, score: int) -> str:
        score_display = f"{self.get_score_display(score)}"
        if not self.get_max_point():
            max_point_display = ""
        else:
            max_point_display = f"/{self.get_max_point_score_display()}"

        return f"{score_display}{max_point_display}"
