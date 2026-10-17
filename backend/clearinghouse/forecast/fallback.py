"""Chalk with a local fallback, so a Chalk outage never fails a clear (design B7).

``FallbackProvider`` asks the primary provider (Chalk) first. If that call raises, it serves the
same request from the local provider and records why in ``methods["fallback_reason"]``; the
result's ``source`` is then ``"local"``, so the API never claims Chalk served a number it did not.
After a failure the primary is skipped for ``cooldown_s`` seconds, so a Chalk outage costs one
timeout, not one per request.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
import time
from typing import Any

from clearinghouse.forecast.provider import ForecastProvider, ForecastResult

log = logging.getLogger(__name__)

COOLDOWN_S = 60.0


class FallbackProvider(ForecastProvider):
    def __init__(self, primary: ForecastProvider, fallback: ForecastProvider,
                 cooldown_s: float = COOLDOWN_S):
        self.primary = primary
        self.fallback = fallback
        self.cooldown_s = cooldown_s
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._last_error = ""

    @property
    def source(self):  # what this provider is configured to serve from
        return self.primary.source

    def __repr__(self) -> str:
        return f"FallbackProvider(primary={self.primary!r}, fallback={self.fallback!r})"

    def _primary_up(self) -> bool:
        return time.monotonic() >= self._down_until

    def _mark_down(self, exc: Exception) -> str:
        reason = f"{type(exc).__name__}: {exc}"[:300]
        with self._lock:
            self._down_until = time.monotonic() + self.cooldown_s
            self._last_error = reason
        log.warning("%s unavailable, serving from %s for %.0f s: %s", self.primary.source,
                    self.fallback.source, self.cooldown_s, reason)
        return reason

    def _skipped_reason(self) -> str:
        return f"skipped after a recent failure ({self._last_error})"

    def get_forecast(self, region: str, date: str) -> ForecastResult:
        reason = self._skipped_reason()
        if self._primary_up():
            try:
                return self.primary.get_forecast(region, date)
            except Exception as exc:  # any Chalk failure: network, HTTP, resolver, bad shape
                reason = self._mark_down(exc)
        fc = self.fallback.get_forecast(region, date)
        methods = {**fc.methods, "fallback_reason": f"{self.primary.source} {reason}"}
        return dataclasses.replace(fc, methods=methods)

    def get_bid_features(self, bid: Any, date: str | None = None) -> dict[str, Any]:
        return self.get_bid_features_many([bid], date)[1][0]

    def get_bid_features_many(self, bids: list[Any], date: str | None = None
                              ) -> tuple[str, list[dict[str, Any]]]:
        if self._primary_up():
            try:
                return self.primary.get_bid_features_many(bids, date)
            except Exception as exc:
                self._mark_down(exc)
        return self.fallback.get_bid_features_many(bids, date)
