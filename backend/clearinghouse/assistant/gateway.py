"""Thin client over the Chalk Model Gateway (OpenAI-compatible; https://docs.chalk.ai/docs/model-gateway).

``base_url = {CHALK_API_HOST}/v1/router``, ``Authorization: Bearer CHALK_ROUTER_API_KEY`` and
``X-Chalk-Env-Id: CHALK_ENVIRONMENT``. Model names carry the connection prefix
(``anthropic/...``). Any auth, network or provider failure raises ``GatewayUnavailable``; callers
fall back to the local assistant and say so. Credentials are never logged.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator

import openai
from openai import OpenAI

from clearinghouse.config import Settings, get_settings

DEFAULT_API_HOST = "https://api.chalk.ai"
DEFAULT_MODEL = "anthropic/claude-sonnet-5-5"
TIMEOUT_S = 30.0
# Thinking is always on for current Claude models and shares max_tokens with the answer: a
# tight cap (700 was tried) is spent on thinking and returns no text. Measured through the
# gateway on a typical assistant turn (2026-10-18): default effort 10.9 s / 1,262 output
# tokens; reasoning_effort="low" 4.5 s / 441. The gateway's Chat route rejects the native
# output_config / thinking fields ("cannot represent ... losslessly"), so effort goes in
# reasoning_effort. Answer length is set by the prompt, not by truncation.
EFFORT = "low"
MAX_TOKENS = 4000


class GatewayUnavailable(RuntimeError):
    """The Model Gateway could not serve the request (no key, auth, network, provider)."""


def _wrap(exc: Exception) -> GatewayUnavailable:
    status = getattr(exc, "status_code", None)
    kind = type(exc).__name__
    return GatewayUnavailable(f"{kind}" + (f" (HTTP {status})" if status else ""))


class Gateway:
    def __init__(self, settings: Settings | None = None):
        s = settings or get_settings()
        self.api_key = s.CHALK_ROUTER_API_KEY
        self.env_id = s.CHALK_ENVIRONMENT
        self.host = (s.CHALK_API_HOST or DEFAULT_API_HOST).rstrip("/")
        self.model = s.CHALK_MODEL or DEFAULT_MODEL
        self.vision_model = s.CHALK_VISION_MODEL or self.model
        self._client: OpenAI | None = None

    def __repr__(self) -> str:  # never include the key
        return f"Gateway(host={self.host!r}, model={self.model!r}, configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.env_id)

    @property
    def client(self) -> OpenAI:
        if not self.configured:
            raise GatewayUnavailable("CHALK_ROUTER_API_KEY or CHALK_ENVIRONMENT not set")
        if self._client is None:
            self._client = OpenAI(base_url=f"{self.host}/v1/router", api_key=self.api_key,
                                  default_headers={"X-Chalk-Env-Id": self.env_id},
                                  timeout=TIMEOUT_S, max_retries=1)
        return self._client

    # No ``temperature``: current Claude models reject it ("`temperature` is deprecated for
    # this model", HTTP 400 through the gateway).
    def chat_stream(self, messages: list[dict], max_tokens: int = MAX_TOKENS,
                    effort: str = EFFORT) -> Iterator[str]:
        """Yield text deltas. Raises ``GatewayUnavailable`` (also mid-stream)."""
        try:
            stream = self.client.chat.completions.create(
                model=self.model, messages=messages, stream=True, max_tokens=max_tokens,
                reasoning_effort=effort)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except GatewayUnavailable:
            raise
        except openai.OpenAIError as exc:
            raise _wrap(exc) from None

    def chat(self, messages: list[dict], max_tokens: int = MAX_TOKENS,
             effort: str = EFFORT) -> str:
        return "".join(self.chat_stream(messages, max_tokens, effort))

    def vision(self, image_bytes: bytes, mime: str, prompt: str, max_tokens: int = MAX_TOKENS,
               effort: str = EFFORT) -> str:
        """One image + prompt -> text (image sent as a base64 data-URL ``image_url`` part)."""
        url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": url}},
        ]}]
        try:
            resp = self.client.chat.completions.create(
                model=self.vision_model, messages=messages, max_tokens=max_tokens,
                reasoning_effort=effort)
            return resp.choices[0].message.content or ""
        except GatewayUnavailable:
            raise
        except openai.OpenAIError as exc:
            raise _wrap(exc) from None
