"""What a gate is given and answers: the candidate page a compile drafted (CandidatePage) and
the gate's verdict on it (GateResult). The compiler and the gates (cairn.gate) both build on
this module, so neither imports the other to name them; cairn.compiler imports both names, as
callers have always found them there."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cairn.store import Claim


class GateResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class CandidatePage(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    body: str
    claims: tuple[Claim, ...]
