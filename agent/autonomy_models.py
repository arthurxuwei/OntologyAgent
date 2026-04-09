from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Stage = Literal[
    "observed",
    "planned",
    "approved_by_policy",
    "executing",
    "confirmed",
    "reconciled",
    "closed",
    "failed",
    "cooldown",
    "paused",
    "circuit_open",
]

IntentType = Literal["trade", "chain", "noop"]


class CircuitBreakerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["closed", "open"] = "closed"
    reason: str | None = None
    openedAt: str | None = None


class RuntimeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intentId: str
    intentType: IntentType
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    confidence: float | None = None
    expiry: str | None = None
    riskTags: list[str] = Field(default_factory=list)
    createdAt: str | None = None
    stage: Stage = "planned"


class RuntimeExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executionId: str
    intentId: str
    intentType: IntentType
    stage: Stage
    status: Literal["active", "completed", "failed"] = "active"
    externalId: str | None = None
    failureCode: str | None = None
    failureMessage: str | None = None


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny", "cooldown", "trip_circuit"]
    reason: str


class RuntimeLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initialized: bool = False
    activeIntents: list[RuntimeIntent] = Field(default_factory=list)
    activeExecutions: list[RuntimeExecutionRecord] = Field(default_factory=list)
    executionHistory: list[RuntimeExecutionRecord] = Field(default_factory=list)
    latestObservation: dict[str, Any] | None = None
    failureCounts: dict[str, int] = Field(default_factory=dict)
    cooldowns: dict[str, str] = Field(default_factory=dict)
    circuitBreaker: CircuitBreakerState = Field(default_factory=CircuitBreakerState)
    lastTickAt: str | None = None
