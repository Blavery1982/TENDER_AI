"""CORE-first router for optional AI escalation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from ai.base import (AI_ASSISTANCE_UNAVAILABLE, AVAILABLE, ERROR,
                     MANUAL_REVIEW_REQUIRED, RESOLVED, AIRequest, AIProvider,
                     CoreDecision, ResolutionResult)
from ai.config import load_ai_config
from ai.core_fallback import core_is_confident, run_core
from ai.openai_provider import OpenAIProvider
from ai.usage import AIUsageLedger
from ai.yandex_provider import YandexAIProvider


class AIRouter:
    def __init__(self, *, config: dict[str, Any] | None = None,
                 providers: dict[str, AIProvider] | None = None,
                 usage: AIUsageLedger | None = None):
        self.config = config or load_ai_config()
        self.mode = str(self.config["ai_mode"]).upper()
        self.providers = providers or {
            "OPENAI": OpenAIProvider(),
            "YANDEX": YandexAIProvider(),
        }
        self.usage = usage or AIUsageLedger(self.config.get("ai_daily_limit"),
                                            self.config.get("ai_monthly_limit"))

    def _order(self) -> tuple[str, ...]:
        if self.mode == "AUTO":
            return ("OPENAI", "YANDEX")
        if self.mode in {"OPENAI", "YANDEX"}:
            if self.config.get("explicit_mode_fallback_to_other_ai"):
                other = "YANDEX" if self.mode == "OPENAI" else "OPENAI"
                return (self.mode, other)
            return (self.mode,)
        return ()

    def resolve(self, task: str, payload: Any, *, reason: str,
                core_solver: Callable[[Any], CoreDecision]) -> ResolutionResult:
        core = run_core(core_solver, payload)
        if core_is_confident(core):
            return ResolutionResult(RESOLVED, core.value, "CORE", core.reason,
                                    core.status, provider_attempts=())

        attempts: list[dict[str, Any]] = []
        if self.mode != "OFF":
            for name in self._order():
                provider = self.providers.get(name)
                if provider is None:
                    attempts.append({"provider": name, "status": "UNAVAILABLE",
                                     "reason": "Provider не зарегистрирован"})
                    continue
                health = provider.health_check()
                attempts.append({"provider": name, "status": health.status,
                                 "reason": health.reason})
                if health.status != AVAILABLE or not health.available:
                    continue
                if not self.usage.allowed():
                    attempts.append({"provider": name, "status": "LIMIT_REACHED",
                                     "reason": "Достигнут лимит AI-вызовов"})
                    continue
                request = AIRequest(task, deepcopy(payload), reason)
                try:
                    response = provider.analyze(
                        request, timeout_seconds=float(self.config["request_timeout_seconds"])
                    )
                except (TimeoutError, ConnectionError) as exc:
                    status = "TIMEOUT" if isinstance(exc, TimeoutError) else ERROR
                    self.usage.record(name, reason, status)
                    attempts.append({"provider": name, "status": status,
                                     "reason": type(exc).__name__})
                    continue
                except Exception as exc:
                    self.usage.record(name, reason, ERROR)
                    attempts.append({"provider": name, "status": ERROR,
                                     "reason": type(exc).__name__})
                    continue
                self.usage.record(name, reason, response.status, response.approximate_cost)
                attempts.append({"provider": name, "status": response.status,
                                 "reason": response.reason,
                                 "approximate_cost": response.approximate_cost})
                if response.status == RESOLVED:
                    return ResolutionResult(
                        RESOLVED, response.value, name, response.reason, core.status,
                        ai_status=response.status, provider_attempts=tuple(attempts),
                        core_rule_candidate=deepcopy(response.core_rule_candidate),
                    )

        fallback_status = (AI_ASSISTANCE_UNAVAILABLE if self.mode == "OFF"
                           else MANUAL_REVIEW_REQUIRED)
        fallback_reason = ("AI отключён; CORE не смог принять надёжное решение"
                           if self.mode == "OFF" else
                           "CORE не уверен, доступный AI не дал надёжного результата")
        return ResolutionResult(fallback_status, core.value, "CORE", fallback_reason,
                                core.status, ai_status=AI_ASSISTANCE_UNAVAILABLE,
                                provider_attempts=tuple(attempts))
