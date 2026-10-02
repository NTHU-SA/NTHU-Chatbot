"""單一實例的突發限流。"""

from __future__ import annotations

import time
from collections import OrderedDict


class RateLimiter:
    """
    單一實例的 token bucket 限流（防突發），以內部 user id 為 key。

    每日額度另外存在 user store，才能跨 Cloud Run 實例。
    """

    def __init__(self, rate_per_minute: int = 20, burst: int = 10, max_keys: int = 10_000):
        self._rate = rate_per_minute / 60.0
        self._burst = float(burst)
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._max_keys = max_keys

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return allowed
