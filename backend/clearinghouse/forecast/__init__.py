"""Day-ahead grid forecast (M4): models, providers and the provider switch (design B2, B7).

``get_provider()`` returns ``ChalkForecastProvider`` iff every ``CHALK_*`` variable is set
(``clearinghouse.config.chalk_configured()``), else the ``LocalForecastProvider``. Each
``ForecastResult.source`` reports which one served it ("chalk" / "local").
"""

from __future__ import annotations

from functools import lru_cache

from clearinghouse import config
from clearinghouse.forecast.provider import (
    BID_FEATURE_NAMES,
    ForecastProvider,
    ForecastResult,
    bid_features,
)


@lru_cache(maxsize=1)
def _local_provider():
    from clearinghouse.forecast.local import LocalForecastProvider

    return LocalForecastProvider()


def get_provider() -> ForecastProvider:
    """Chalk iff configured; otherwise the (process-wide, cached) local provider."""
    if config.chalk_configured():
        from clearinghouse.forecast.chalk import ChalkForecastProvider

        return ChalkForecastProvider.from_settings(config.get_settings())
    return _local_provider()


__all__ = ["BID_FEATURE_NAMES", "ForecastProvider", "ForecastResult", "bid_features",
           "get_provider"]
