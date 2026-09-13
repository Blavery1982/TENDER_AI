"""In-memory AI call accounting without prompts, documents or credentials."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class UsageEvent:
    checked_at: str
    provider: str
    reason: str
    status: str
    approximate_cost: float | None


class AIUsageLedger:
    def __init__(self, daily_limit: int | None = None, monthly_limit: int | None = None):
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self.events: list[UsageEvent] = []

    def allowed(self) -> bool:
        now = datetime.now(timezone.utc)
        daily = sum(event.checked_at[:10] == now.date().isoformat() for event in self.events)
        monthly = sum(event.checked_at[:7] == now.strftime("%Y-%m") for event in self.events)
        return not ((self.daily_limit is not None and daily >= self.daily_limit)
                    or (self.monthly_limit is not None and monthly >= self.monthly_limit))

    def record(self, provider: str, reason: str, status: str,
               approximate_cost: float | None = None) -> None:
        self.events.append(UsageEvent(datetime.now(timezone.utc).isoformat(), provider,
                                      reason, status, approximate_cost))
