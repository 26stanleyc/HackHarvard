"""Chalk Function ``extract-bid``: a document image -> a prefilled flexibility bid.

Runs in a Chalk sandbox (not on the app's server), with the app's own extraction code
(``clearinghouse.assistant.extract``) and the Model Gateway key injected from the Chalk secret
``CHALK_ROUTER_API_KEY``. The vision call goes through the Chalk Model Gateway.

Deploy (and smoke-test on a fixture) from ``backend/`` with the developer's ``chalk login``:

    uv run python chalk_compute/extract_bid.py

Running this file deploys a new version (chalkcompute deploys on import). The API calls the
deployed function by name: ``chalkcompute.RemoteFunction.from_name("extract-bid")``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import chalkcompute

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

FUNCTION_NAME = "extract-bid"  # Chalk function names are DNS labels (no underscores)
ENV_ID = "hllr1480fa66"
MODEL = "anthropic/claude-sonnet-5-5"

IMAGE = (
    chalkcompute.Image.debian_slim("3.12")
    .pip_install(["pydantic==2.14.1", "numpy==2.5.4", "openai==3.24.0", "python-dotenv==1.2.4"])
    .add_local_python_source("clearinghouse")
)


@chalkcompute.function(
    name=FUNCTION_NAME, image=IMAGE, call_timeout=90.0, retries=1,
    secrets=[chalkcompute.Secret.from_chalk_env("CHALK_ROUTER_API_KEY")],
    env={"CHALK_ENVIRONMENT": ENV_ID, "CHALK_API_HOST": "https://api.chalk.ai",
         "CHALK_MODEL": MODEL, "CHALK_VISION_MODEL": MODEL},
)
def extract_bid(image_b64: str, mime: str) -> str:
    """Returns JSON: the validated bid, or ``{"error": ...}`` (never raises for bad input)."""
    import base64
    import binascii
    import json

    from clearinghouse.assistant.extract import ExtractError
    from clearinghouse.assistant.extract import extract_bid as run
    from clearinghouse.assistant.gateway import GatewayUnavailable

    try:
        data = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError):
        return json.dumps({"error": "image_b64 is not valid base64"})
    try:
        return json.dumps(run(data, mime))
    except ExtractError as exc:
        return json.dumps({"error": str(exc)})
    except GatewayUnavailable as exc:
        return json.dumps({"error": f"Chalk Model Gateway unavailable: {exc}"})


if __name__ == "__main__":
    import base64

    sample = BACKEND.parent / "fixtures" / "extract" / "bill_01.png"
    out = extract_bid.remote(base64.b64encode(sample.read_bytes()).decode(), "image/png")
    print("deployed", FUNCTION_NAME, "->", out)
