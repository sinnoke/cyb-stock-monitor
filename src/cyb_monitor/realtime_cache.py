from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
import threading
from typing import Callable

from .models import MinuteBar


class MinuteKlineCache:
    def __init__(self, max_bars: int = 5) -> None:
        self.max_bars = max_bars
        self._lock = threading.RLock()
        self._bars: dict[str, deque[MinuteBar]] = defaultdict(lambda: deque(maxlen=max_bars))
        self._names: dict[str, str] = {}

    def update(self, bar: MinuteBar) -> None:
        with self._lock:
            bars = self._bars[bar.code]
            if bars and bars[-1].time == bar.time:
                bars[-1] = bar
            else:
                bars.append(bar)
            if bar.name:
                self._names[bar.code] = bar.name

    def snapshot(self, min_bars: int = 5) -> dict[str, list[MinuteBar]]:
        with self._lock:
            return {
                code: list(bars)
                for code, bars in self._bars.items()
                if len(bars) >= min_bars
            }

    def total_codes(self) -> int:
        with self._lock:
            return len(self._bars)


class PeriodicKlineAnalyzer:
    def __init__(
        self,
        cache: MinuteKlineCache,
        interval_seconds: int = 300,
        min_bars: int = 5,
        top_n: int = 10,
        analyze: Callable[[dict[str, list[MinuteBar]]], None] | None = None,
    ) -> None:
        self.cache = cache
        self.interval_seconds = interval_seconds
        self.min_bars = min_bars
        self.top_n = top_n
        self.analyze = analyze or self._default_analyze
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="kline-analyzer", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            snapshot = self.cache.snapshot(min_bars=self.min_bars)
            self.analyze(snapshot)

    def _default_analyze(self, snapshot: dict[str, list[MinuteBar]]) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[analysis] {now} ready_codes={len(snapshot)} "
            f"cached_codes={self.cache.total_codes()} min_bars={self.min_bars}",
            flush=True,
        )
        ranked = sorted(
            snapshot.items(),
            key=lambda item: sum(bar.volume for bar in item[1]),
            reverse=True,
        )
        for code, bars in ranked[: self.top_n]:
            latest = bars[-1]
            volume_sum = sum(bar.volume for bar in bars)
            print(
                f"[analysis] {code} {latest.name} "
                f"latest={latest.time:%Y-%m-%d %H:%M:%S} "
                f"last_{len(bars)}_volume={volume_sum}",
                flush=True,
            )
