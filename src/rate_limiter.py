"""Generic synchronous rate limiter for API calls."""

import time
from collections import deque


class RateLimiter:
    """Token-bucket-style rate limiter with per-second, per-minute, and per-day windows.

    Usage:
        limiter = RateLimiter(max_calls_per_second=2, max_calls_per_minute=30, max_calls_per_day=500)
        for item in items:
            limiter.wait()
            call_api(item)
    """

    def __init__(
        self,
        max_calls_per_second: int = 0,
        max_calls_per_minute: int = 0,
        max_calls_per_day: int = 0,
    ) -> None:
        self.limits: list[tuple[int, float]] = []
        if max_calls_per_second > 0:
            self.limits.append((max_calls_per_second, 1.0))
        if max_calls_per_minute > 0:
            self.limits.append((max_calls_per_minute, 60.0))
        if max_calls_per_day > 0:
            self.limits.append((max_calls_per_day, 86400.0))

        # One deque of timestamps per window
        self.windows: list[deque[float]] = [deque() for _ in self.limits]

    def wait(self) -> None:
        """Block until the next call is allowed under all rate windows."""
        while True:
            now = time.monotonic()
            sleep_needed = 0.0

            for (max_calls, window_sec), timestamps in zip(self.limits, self.windows):
                # Evict expired timestamps
                while timestamps and timestamps[0] <= now - window_sec:
                    timestamps.popleft()

                if len(timestamps) >= max_calls:
                    # Must wait until the oldest timestamp exits the window
                    wait_until = timestamps[0] + window_sec
                    sleep_needed = max(sleep_needed, wait_until - now)

            if sleep_needed > 0:
                time.sleep(sleep_needed)
            else:
                break

        # Record this call
        now = time.monotonic()
        for ts in self.windows:
            ts.append(now)
