"""Deterministic CORE boundary and safe final fallback."""
from __future__ import annotations

from typing import Any, Callable

from ai.base import (AMBIGUOUS, ERROR, LOW_CONFIDENCE, RESOLVED, UNKNOWN,
                     CoreDecision)

UNCERTAIN_CORE_STATUSES = frozenset({UNKNOWN, AMBIGUOUS, LOW_CONFIDENCE, ERROR})


def run_core(core_solver: Callable[[Any], CoreDecision], payload: Any) -> CoreDecision:
    try:
        result = core_solver(payload)
    except Exception as exc:
        return CoreDecision(ERROR, reason=f"CORE error: {type(exc).__name__}")
    if not isinstance(result, CoreDecision):
        return CoreDecision(ERROR, reason="CORE вернул результат неизвестного формата")
    return result


def core_is_confident(decision: CoreDecision) -> bool:
    return decision.status == RESOLVED
