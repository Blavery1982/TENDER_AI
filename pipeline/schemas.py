"""Схема и безопасный русский пользовательский вывод."""
from __future__ import annotations

import math
from typing import Any


def user_value(value: Any) -> Any:
    """Технические пропуски превращаются в понятное значение только в UI-секции."""
    if value is None or isinstance(value, float) and math.isnan(value):
        return "Нет данных"
    if isinstance(value, dict):
        return {key:user_value(item) for key,item in value.items()}
    if isinstance(value, list):
        return [user_value(item) for item in value]
    return value


def stage(name: str, status: str, detail: str = "") -> dict:
    return {"stage":name,"status":status,"detail":detail}

