"""Configuration: repo-root ``.env`` loading, key status, and path constants.

Key hygiene (design B7): values loaded from ``.env`` are never printed, logged or
returned by anything in this module except the ``Settings`` attributes themselves,
which callers must treat as secrets. ``key_status()`` and the CLI report only
"set" / "missing" by key name.

CLI::

    uv run python -m clearinghouse.config
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

# --- Paths (resolved relative to this file, so they work from any cwd) -------------------------
BACKEND_DIR: Path = Path(__file__).resolve().parent.parent
REPO_ROOT: Path = BACKEND_DIR.parent
ENV_FILE: Path = REPO_ROOT / ".env"
DATA_RAW: Path = BACKEND_DIR / "data" / "raw"
DATA_PROCESSED: Path = BACKEND_DIR / "data" / "processed"
RESULTS_DIR: Path = REPO_ROOT / "docs" / "results"
FIXTURES_DIR: Path = REPO_ROOT / "fixtures"

KEY_NAMES: tuple[str, ...] = (
    "EIA_API_KEY",
    "CHALK_CLIENT_ID",
    "CHALK_CLIENT_SECRET",
    "CHALK_ENVIRONMENT",
    "DATA_GOV_API_KEY",
    "ERCOT_USERNAME",
    "ERCOT_PASSWORD",
    "ERCOT_SUBSCRIPTION_KEY",
)
CHALK_KEYS: tuple[str, ...] = ("CHALK_CLIENT_ID", "CHALK_CLIENT_SECRET", "CHALK_ENVIRONMENT")

KeyState = Literal["set", "missing"]


def _secret() -> str:
    # repr=False keeps values out of repr()/str() of Settings.
    return field(default="", repr=False)


@dataclass(frozen=True)
class Settings:
    """Secret settings. Never print, log or serialize the attribute values."""

    EIA_API_KEY: str = _secret()
    CHALK_CLIENT_ID: str = _secret()
    CHALK_CLIENT_SECRET: str = _secret()
    CHALK_ENVIRONMENT: str = _secret()
    DATA_GOV_API_KEY: str = _secret()
    ERCOT_USERNAME: str = _secret()
    ERCOT_PASSWORD: str = _secret()
    ERCOT_SUBSCRIPTION_KEY: str = _secret()

    @classmethod
    def from_env(cls) -> Settings:
        return cls(**{f.name: os.environ.get(f.name, "").strip() for f in fields(cls)})

    def __repr__(self) -> str:  # explicit: never include values
        return f"Settings({', '.join(f'{k}={v}' for k, v in self.key_status().items())})"

    __str__ = __repr__

    def key_status(self) -> dict[str, KeyState]:
        return {name: ("set" if getattr(self, name) else "missing") for name in KEY_NAMES}

    def chalk_configured(self) -> bool:
        return all(getattr(self, name) for name in CHALK_KEYS)


def load_settings(env_file: Path | None = None) -> Settings:
    """Load ``.env`` (repo root by default) into the environment, then build Settings.

    Existing environment variables take precedence over ``.env`` (override=False).
    """
    path = ENV_FILE if env_file is None else env_file
    if path.is_file():
        load_dotenv(path, override=False)
    return Settings.from_env()


def get_settings() -> Settings:
    return load_settings()


def key_status() -> dict[str, KeyState]:
    """Map each key name to "set" or "missing". Never returns values."""
    return get_settings().key_status()


def chalk_configured() -> bool:
    """True only if every CHALK_* variable is non-empty."""
    return get_settings().chalk_configured()


def main() -> None:
    settings = get_settings()
    print(f".env file: {'found' if ENV_FILE.is_file() else 'not found'} (repo root)")
    for name, state in settings.key_status().items():
        print(f"{name}: {state}")
    print(f"chalk_configured: {settings.chalk_configured()}")


if __name__ == "__main__":
    main()
