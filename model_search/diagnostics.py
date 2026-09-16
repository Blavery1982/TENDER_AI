"""Безопасные диагностические сведения о блокировках внешних страниц."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class BlockedSourceError(PermissionError):
    """PermissionError с безопасной диагностикой, без тела страницы и cookies."""

    def __init__(self, message: str, diagnostic: dict):
        super().__init__(message)
        self.diagnostic = diagnostic


def safe_page_url(value: str | None) -> str:
    """Оставить только адрес страницы без query, fragment и учётных данных."""
    parsed = urlsplit(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "/", "", ""))


def block_markers(text: str) -> list[str]:
    """Вернуть только названия найденных маркеров, не сохраняя текст страницы."""
    markers = []
    if re.search(r"(?i)unusual\s+traffic|not\s+a\s+robot|необычн.{0,20}трафик", text or ""):
        markers.append("robot_check_text")
    if re.search(r"(?i)captcha|подтвердите.{0,35}(?:не робот|вы человек)|пройдите.{0,15}captcha", text or ""):
        markers.append("captcha_text")
    if re.search(r"(?i)доступ\s+ограничен", text or ""):
        markers.append("access_limited_text")
    return markers


def save_snapshot(page, directory: Path | None, prefix: str) -> str | None:
    """Сохранить видимый снимок только в локальный каталог запуска."""
    if directory is None:
        return None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        number = len(list(directory.glob(f"{prefix}_*.png"))) + 1
        path = directory / f"{prefix}_{number}.png"
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return None


def make_block_diagnostic(*, stage: str, reason: str, status: int | None,
                          url: str | None, snapshot: str | None = None,
                          markers: list[str] | None = None,
                          primary: bool = True) -> dict:
    return {
        "stage": stage,
        "classification": reason,
        "reason": reason,
        "http_status": status,
        "url": safe_page_url(url),
        "snapshot": snapshot,
        "markers": list(markers or []),
        "primary": bool(primary),
    }


__all__ = [
    "BlockedSourceError", "safe_page_url", "block_markers",
    "save_snapshot", "make_block_diagnostic",
]
