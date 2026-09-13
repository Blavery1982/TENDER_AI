"""Provider-neutral contracts for optional AI escalation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

AI_MODES = frozenset({"AUTO", "OPENAI", "YANDEX", "OFF"})

RESOLVED = "RESOLVED"
UNKNOWN = "UNKNOWN"
AMBIGUOUS = "AMBIGUOUS"
LOW_CONFIDENCE = "LOW_CONFIDENCE"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
AI_ASSISTANCE_UNAVAILABLE = "AI_ASSISTANCE_UNAVAILABLE"
CORE_RULE_CANDIDATE = "CORE_RULE_CANDIDATE"

CONFIGURED = "CONFIGURED"
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
ERROR = "ERROR"


@dataclass(frozen=True)
class AIRequest:
    task: str
    payload: Any
    reason: str


@dataclass(frozen=True)
class CoreDecision:
    status: str
    value: Any = None
    confidence: float | None = None
    reason: str = ""
    evidence: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    status: str
    configured: bool
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class ProviderResponse:
    provider: str
    status: str
    value: Any = None
    reason: str = ""
    approximate_cost: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    core_rule_candidate: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResolutionResult:
    status: str
    value: Any
    source: str
    reason: str
    core_status: str
    ai_status: str | None = None
    provider_attempts: tuple[dict[str, Any], ...] = ()
    core_rule_candidate: dict[str, Any] | None = None


class AIProvider(Protocol):
    name: str

    def health_check(self) -> ProviderHealth: ...
    def analyze(self, request: AIRequest, *, timeout_seconds: float) -> ProviderResponse: ...
