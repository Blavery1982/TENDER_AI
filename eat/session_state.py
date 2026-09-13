"""Безопасное локальное хранение авторизованной браузерной сессии ЕАТ.

Файл storage state содержит cookies и данные web storage, поэтому считается
секретом: он хранится только в ``secrets/``, имеет права 0600 и никогда не
читается для вывода в лог или отчёт.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_PATH = PROJECT_ROOT / "secrets" / "eat_session_state.json"


def state_path() -> Path:
    configured = os.environ.get("EAT_SESSION_STATE_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_STATE_PATH


def _valid_state(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(value, dict) and isinstance(value.get("cookies"), list)


def has_saved_session() -> bool:
    """Проверить наличие структуры state, не раскрывая её содержимое."""
    return _valid_state(state_path())


def new_eat_context(browser: Any, **options: Any) -> Any:
    """Создать context, сначала восстановив сохранённую сессию, если она есть."""
    path = state_path()
    if _valid_state(path):
        options["storage_state"] = str(path)
    return browser.new_context(**options)


def save_eat_session(context: Any) -> Path:
    """Атомарно сохранить только уже подтверждённую вызывающим кодом сессию."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        try:
            context.storage_state(path=str(temporary), indexed_db=True)
        except TypeError:  # Совместимость с тестовыми объектами/старыми Playwright.
            context.storage_state(path=str(temporary))
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def run_manual_session_save() -> int:
    """Один ручной bootstrap без чтения закупок и без автоматических повторов."""
    from playwright.sync_api import sync_playwright
    from eat.browser_auth import ENTRY_URL, PlaywrightLoginUI

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = new_eat_context(browser, locale="ru-RU", service_workers="block")
        page = context.new_page()
        try:
            page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(2_000)
            if not PlaywrightLoginUI(page).signed_in():
                print(
                    "Войдите в ЕАТ вручную. Окно само определит успешный вход и "
                    "сохранит сессию; logout выполнять не нужно.",
                    flush=True,
                )
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline and not PlaywrightLoginUI(page).signed_in():
                    page.wait_for_timeout(500)
            if not PlaywrightLoginUI(page).signed_in():
                print("Авторизация ЕАТ не подтверждена за 10 минут; повторные попытки не выполняются.")
                return 1
            save_eat_session(context)
            print("Авторизованная сессия ЕАТ безопасно сохранена.")
            return 0
        finally:
            context.close()
            browser.close()
