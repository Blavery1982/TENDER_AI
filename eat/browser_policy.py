"""Единая production-политика Chromium для ЕАТ на macOS."""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

from eat.browser_auth import ENTRY_URL, PlaywrightLoginUI
from eat.session_state import has_saved_session, new_eat_context, save_eat_session


def _minimize_chromium() -> None:
    """Best effort: отсутствие разрешения macOS не ломает pipeline."""
    if sys.platform != "darwin":
        return
    script = (
        'tell application "System Events" to tell every application process '
        'whose name contains "Chromium" to set value of attribute "AXMinimized" '
        'of every window to true'
    )
    try:
        subprocess.run(["osascript", "-e", script], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=3, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


@dataclass
class AuthorizedEatBrowser:
    browser: Any
    context: Any
    page: Any
    interactive: bool

    def close(self) -> None:
        # Сохраняем возможное продление cookies перед закрытием, logout не вызываем.
        save_eat_session(self.context)
        self.context.close()
        self.browser.close()


def _launch(playwright: Any, *, interactive: bool) -> tuple[Any, Any, Any]:
    args = ["--start-minimized"] if interactive else []
    browser = playwright.chromium.launch(headless=not interactive, args=args)
    context = new_eat_context(browser, locale="ru-RU", accept_downloads=True,
                              service_workers="block")
    return browser, context, context.new_page()


def open_authorized_eat_browser(playwright: Any, *, timeout_seconds: int = 600,
                                notify=print) -> AuthorizedEatBrowser:
    """Headless при живом state; одно видимое окно только при истёкшем state."""
    modes = [False, True] if has_saved_session() else [True]
    for interactive in modes:
        browser, context, page = _launch(playwright, interactive=interactive)
        try:
            if interactive:
                _minimize_chromium()
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            if PlaywrightLoginUI(page).signed_in():
                save_eat_session(context)
                if interactive:
                    _minimize_chromium()
                return AuthorizedEatBrowser(browser, context, page, interactive)
            if not interactive:
                context.close()
                browser.close()
                continue
            notify("Сессия ЕАТ истекла. Требуется один ручной вход в открытом Chromium.")
            page.bring_to_front()
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline and not PlaywrightLoginUI(page).signed_in():
                page.wait_for_timeout(500)
            if not PlaywrightLoginUI(page).signed_in():
                raise RuntimeError("Авторизация ЕАТ не подтверждена; повторные попытки не выполняются")
            save_eat_session(context)
            _minimize_chromium()
            return AuthorizedEatBrowser(browser, context, page, interactive)
        except Exception:
            try:
                context.close()
                browser.close()
            except Exception:
                pass
            raise
    raise RuntimeError("Не удалось восстановить авторизованную сессию ЕАТ")
