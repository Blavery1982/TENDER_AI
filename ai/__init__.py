"""Optional AI layer. TENDER_AI CORE never depends on this package's providers."""

from ai.base import (AI_ASSISTANCE_UNAVAILABLE, MANUAL_REVIEW_REQUIRED,
                     RESOLVED, CoreDecision, ResolutionResult)
from ai.router import AIRouter

__all__ = [
    "AIRouter", "CoreDecision", "ResolutionResult", "RESOLVED",
    "MANUAL_REVIEW_REQUIRED", "AI_ASSISTANCE_UNAVAILABLE",
]
