"""The classified inference request the gateway routes."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Privacy(str, Enum):
    standard = "standard"
    local_only = "local_only"  # data must never leave the device / remote providers


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)  # idempotency key; ledger dedups on this
    user_id: str
    org_id: str = ""
    workspace: str = ""
    industry: str = ""
    role: str = ""
    country: str = "US"
    language: str = "en"
    task_type: str = "chat"
    privacy: Privacy = Privacy.standard
    required_capabilities: tuple[str, ...] = ("text",)
    est_input_tokens: int = 0
    est_output_tokens: int = 0
