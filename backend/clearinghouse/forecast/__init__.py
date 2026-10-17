"""Day-ahead grid forecast (M4): models, providers and the provider switch (design B2, B7).

``get_provider()`` returns ``ChalkForecastProvider`` (wrapped in ``FallbackProvider``, which
serves from the local model if a Chalk call fails) iff every ``CHALK_*`` variable is set
(``clearinghouse.config.chalk_configured()``), else the ``LocalForecastProvider``. Each
``ForecastResult.source`` reports which one actually served it ("chalk" / "local").
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


@lru_cache(maxsize=1)
def _chalk_provider(client_id: str, client_secret: str, environment: str):
    """One Chalk client per credential set, so the OAuth token and connections are reused."""
    from clearinghouse.forecast.chalk import ChalkForecastProvider
    from clearinghouse.forecast.fallback import FallbackProvider

    chalk = ChalkForecastProvider(client_id, client_secret, environment,
                                  timeout_s=CHALK_TIMEOUT_S)
    return FallbackProvider(chalk, _local_provider())


CHALK_TIMEOUT_S = 5.0


def get_provider() -> ForecastProvider:
    """Chalk (falling back to local on failure) iff configured; otherwise the local provider.

    Both are process-wide and cached: the API calls this on every request.
    """
    s = config.get_settings()
    if s.chalk_configured():
        return _chalk_provider(s.CHALK_CLIENT_ID, s.CHALK_CLIENT_SECRET, s.CHALK_ENVIRONMENT)
    return _local_provider()


__all__ = ["BID_FEATURE_NAMES", "ForecastProvider", "ForecastResult", "bid_features",
           "get_provider"]
