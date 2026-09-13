"""Presence-only probes for optional AI credentials in macOS Keychain.

No setup command is intentionally exposed yet. This module never reads a
secret value: it only checks whether a provider-specific Keychain item exists.
"""
from __future__ import annotations

import ctypes as C

from security.eat_credentials import MacOSKeychain

AI_KEYCHAIN_ENTRIES = {
    "OPENAI": ("TENDER_AI_OPENAI", "api_credentials"),
    "YANDEX": ("TENDER_AI_YANDEX_AI", "api_credentials"),
}


class AIKeychainPresence(MacOSKeychain):
    def __init__(self, provider: str):
        try:
            self.service, self.account = AI_KEYCHAIN_ENTRIES[provider.upper()]
        except KeyError as exc:
            raise ValueError("Неизвестный AI provider") from exc
        super().__init__()

    def _find(self, read: bool = False):
        # Presence check only: credential bytes are never requested.
        item = C.c_void_p()
        service = self.service.encode()
        account = self.account.encode()
        status = self.api.SecKeychainFindGenericPassword(
            None, len(service), service, len(account), account,
            None, None, C.byref(item),
        )
        if status == -25300:
            return None, None
        self._check(status)
        return item, None


def ai_credentials_configured(provider: str, *, keychain=None) -> bool:
    backend = keychain if keychain is not None else AIKeychainPresence(provider)
    return bool(backend.exists())
