from ..lib import lq as liblq
from ..utils.api.remote_const import (
    LEVEL_KONTEN,
    PLAYER_RANKS,
    LEVEL_MAX_POINTS,
    LEVEL_MAX_POINT_KONTEN,
)


class MajsoulLevel:
    def __init__(self, level: liblq.AccountLevel):
        real_id = level.id % 10000
        self.id = level.id
        self.score = level.score
        self._major_rank = real_id // 100
        self._minor_rank = real_id % 100
        self._num_player_id = level.id // 10000

    def to_level_id(self):
        return (
            self._num_player_id * 10000
            + self._major_rank * 100
            + self._minor_rank
        )

    def is_konten(self) -> bool:
        return self._major_rank >= LEVEL_KONTEN - 1

    def get_tag(self) -> str:
        label = PLAYER_RANKS[
            LEVEL_KONTEN - 2 if self.is_konten() else self._major_rank - 1
        ]
        if self._minor_rank == LEVEL_KONTEN - 1:
            return label
        if self._minor_rank == 1:
            return label + "一"
        elif self._minor_rank == 2:
            return label + "二"
        elif self._minor_rank == 3:
            return label + "三"
        else:
            raise ValueError(f"Unknown minor rank: {self._minor_rank}")

    def get_max_point(self) -> int:
        if self.is_konten():
            if self._minor_rank == 20:
                return 0
            return LEVEL_MAX_POINT_KONTEN
        return LEVEL_MAX_POINTS[
            (self._major_rank - 1) * 3 + self._minor_rank - 1
        ]

    def get_starting_point(self) -> int:
        if self._major_rank == 1:
            return 0
        return self.get_max_point() // 2

    def get_version_adjusted_score(self) -> int:
        if self._major_rank == LEVEL_KONTEN - 1:
            return (self.score // 100) * 10 + 200
        return self.score

    def get_score_display(self) -> str:
        score = self.get_version_adjusted_score()
        if self.is_konten():
            return f"{score / 100:.1f}"
        return str(score)

    def get_max_point_score_display(self) -> str:
        max_point = self.get_max_point()
        if self.is_konten():
            return f"{max_point / 100:.1f}"
        return str(max_point)

    def formatAdjustedScoreWithTag(self) -> str:
        return f"{self.get_tag()} {self.formatAdjustedScore()}"

    def formatAdjustedScore(self) -> str:
        return f"{self.get_score_display()}{'' if not self.get_max_point() else '/' + self.get_max_point_score_display()}"  # noqa: E501
