"""移植客户端 RouteDelayWatcher.lua / NetworkQualityAnalyzer.lua，时间单位都是秒。

心跳里的 delay 和 network_quality 由这里算出，请求超时也由这里判定。
"""

from .constants import DELAY_INF, RPC_TIMEOUT

QUALITY_WINDOW = 30.0
QUALITY_SAMPLES = 10
LATENCY_WEIGHT = 0.7
STABILITY_WEIGHT = 0.3
RECENT_WINDOW = 2.0
RECENT_WEIGHT = 0.3
# _CheckWaitingResponse 发现有请求超时、且没有还在等的请求时，返回的等待时长
TIMEOUT_WAIT = 2.0


class NetworkQualityAnalyzer:
    def __init__(self) -> None:
        self._samples: list[tuple[float, float]] = []

    def clear(self) -> None:
        self._samples = []

    def _drop_old(self, now: float) -> None:
        cutoff = now - QUALITY_WINDOW
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)

    def add_latency_sample(self, now: float, latency: float) -> None:
        self._drop_old(now)
        self._samples.append((now, latency))

    def refresh_last_sample(self, now: float, latency: float) -> None:
        self._drop_old(now)
        if self._samples:
            self._samples[-1] = (now, latency)
        else:
            self._samples.append((now, latency))

    def stability_score(self) -> float:
        """近 30 秒里第二大的延迟。"""
        if not self._samples:
            return DELAY_INF
        largest = second = self._samples[0][1]
        for _, latency in self._samples[1:]:
            if latency >= largest:
                second, largest = largest, latency
            elif latency >= second:
                second = latency
        return second

    def network_quality(self) -> float:
        if not self._samples:
            return DELAY_INF
        total = weight = 0.0
        for age, (_, latency) in enumerate(reversed(self._samples[-QUALITY_SAMPLES:])):
            total += latency / (age + 1)
            weight += 1.0 / (age + 1)
        score = (total / weight) * LATENCY_WEIGHT + self.stability_score() * STABILITY_WEIGHT
        return score / (LATENCY_WEIGHT + STABILITY_WEIGHT)


class RouteDelayWatcher:
    def __init__(self) -> None:
        # 按发出顺序记录还没回包的请求：序号 → 发出时间
        self._waiting: dict[int, float] = {}
        self._recent: list[tuple[float, float]] = []
        self._analyzer = NetworkQualityAnalyzer()
        self.delay = DELAY_INF
        self.quality = DELAY_INF

    def clear(self) -> None:
        self._waiting = {}
        self._recent = []
        self._analyzer.clear()
        self.delay = DELAY_INF
        self.quality = DELAY_INF

    def add_waiting(self, index: int, now: float) -> None:
        self._waiting[index] = now

    def receive(self, index: int, now: float) -> None:
        if index in self._waiting:
            self._recent.append((now, now - self._waiting.pop(index)))

    def tick(self, now: float) -> list[int]:
        """每 0.5 秒调一次，更新 delay / quality，返回刚超时的请求序号。"""
        recent = self._recent_delay(now)
        waiting, timed_out = self._check_waiting(now)
        total = weight = 0.0
        if recent >= 0:
            total += recent * RECENT_WEIGHT
            weight += RECENT_WEIGHT
        if waiting >= 0:
            if self.delay < DELAY_INF:
                waiting = max(waiting, self.delay)
            total += waiting
            weight += 1
        if weight > 0:
            self.delay = total / weight
            self._analyzer.add_latency_sample(now, self.delay)
            self.quality = self._analyzer.network_quality()
        elif 0 < self.delay < DELAY_INF:
            self._analyzer.refresh_last_sample(now, self.delay)
            self.quality = self._analyzer.network_quality()
        return timed_out

    def _recent_delay(self, now: float) -> float:
        self._recent = [(stamp, delay) for stamp, delay in self._recent if stamp + RECENT_WINDOW >= now]
        if not self._recent:
            return -1.0
        return sum(delay for _, delay in self._recent) / len(self._recent)

    def _check_waiting(self, now: float) -> tuple[float, list[int]]:
        waiting = -1.0
        timed_out: list[int] = []
        for index, sent_at in list(self._waiting.items()):
            if now < sent_at + RPC_TIMEOUT:
                return now - sent_at, timed_out
            del self._waiting[index]
            waiting = TIMEOUT_WAIT
            self._recent.append((now, RPC_TIMEOUT))
            timed_out.append(index)
        return waiting, timed_out
