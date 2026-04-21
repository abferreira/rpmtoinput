from __future__ import annotations
from collections import deque

from config import AppConfig


class RPMProcessor:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._window: deque[float] = deque(maxlen=config.smoothing_factor)

    def update_config(self, config: AppConfig) -> None:
        if config.smoothing_factor != self._config.smoothing_factor:
            self._window = deque(maxlen=config.smoothing_factor)
        self._config = config

    def feed(self, raw_rpm: float) -> tuple[float, float]:
        self._window.append(raw_rpm)
        smoothed = sum(self._window) / len(self._window)

        dz = self._config.dead_zone_rpm
        top = self._config.max_rpm
        span = top - dz
        if span <= 0 or smoothed <= dz:
            normalized = 0.0
        else:
            normalized = (smoothed - dz) / span * self._config.rpm_multiplier
            normalized = max(0.0, min(1.0, normalized))

        return smoothed, normalized

    def reset(self) -> None:
        self._window.clear()
