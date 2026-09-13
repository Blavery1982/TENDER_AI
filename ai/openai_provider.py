"""Optional OpenAI adapter shell; no SDK or network is connected yet."""
from __future__ import annotations

from typing import Callable

from ai.base import (AVAILABLE, CONFIGURED, ERROR, RESOLVED, UNAVAILABLE,
                     AIRequest, ProviderHealth, ProviderResponse)
from security.ai_credentials import ai_credentials_configured


class OpenAIProvider:
    name = "OPENAI"

    def __init__(self, *, credential_probe: Callable[[], bool] | None = None,
                 availability_probe: Callable[[], bool] | None = None,
                 client: Callable[[AIRequest, float], ProviderResponse] | None = None):
        self._credential_probe = credential_probe or (lambda: ai_credentials_configured(self.name))
        self._availability_probe = availability_probe
        self._client = client

    def health_check(self) -> ProviderHealth:
        try:
            configured = bool(self._credential_probe())
        except Exception:
            return ProviderHealth(self.name, ERROR, False, False, "Keychain недоступен")
        if not configured:
            return ProviderHealth(self.name, UNAVAILABLE, False, False, "Credentials не настроены")
        if self._client is None or self._availability_probe is None:
            return ProviderHealth(self.name, CONFIGURED, True, False,
                                  "Credentials есть, runtime adapter пока не подключён")
        try:
            available = bool(self._availability_probe())
        except Exception:
            return ProviderHealth(self.name, ERROR, True, False,
                                  "Проверка доступности provider завершилась ошибкой")
        if not available:
            return ProviderHealth(self.name, UNAVAILABLE, True, False,
                                  "Provider недоступен из текущей среды")
        return ProviderHealth(self.name, AVAILABLE, True, True, "Provider готов")

    def analyze(self, request: AIRequest, *, timeout_seconds: float) -> ProviderResponse:
        if self._client is None:
            return ProviderResponse(self.name, UNAVAILABLE, reason="Runtime adapter не подключён")
        response = self._client(request, timeout_seconds)
        if not isinstance(response, ProviderResponse):
            return ProviderResponse(self.name, ERROR, reason="Некорректный ответ provider")
        return response
