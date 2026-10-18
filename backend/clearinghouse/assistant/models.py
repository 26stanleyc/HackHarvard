"""Request bodies for /api/assistant/*.

Bids here are lenient on purpose: the assistant must be able to talk about a bid while it is
being edited and is still invalid (deadline before start, missing numbers). The clearing API's
strict ``schemas.Bid`` is not used for these requests.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from clearinghouse.assistant.personas import DEFAULT_PERSONA, PersonaId

MAX_MESSAGES = 20
MAX_BIDS = 30


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssistantBid(_Model):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)
    load_type: str = Field(default="", max_length=64)
    energy_mwh: float | None = None
    earliest_hour: int | None = None
    deadline_hour: int | None = None
    max_mw: float | None = None


class Scenario(_Model):
    carbon_price: float | None = None
    pool_usd: float | None = None
    background_pct: float | None = None


class ChatMessage(_Model):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class SuggestRequest(_Model):
    day: str
    bids: list[AssistantBid] = Field(default_factory=list, max_length=MAX_BIDS)
    persona: PersonaId = DEFAULT_PERSONA
    scenario: Scenario | None = None


class ChatRequest(SuggestRequest):
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    focus_field: str | None = Field(default=None, max_length=64)  # set by the field "?" icons
