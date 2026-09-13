"""Non-secret global AI configuration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AI_CONFIG_PATH = ROOT / "config" / "ai.json"
DEFAULT_AI_MODE = "AUTO"
VALID_MODES = {"AUTO", "OPENAI", "YANDEX", "OFF"}


def load_ai_config(path: Path = AI_CONFIG_PATH) -> dict[str, Any]:
    defaults = {
        "ai_mode": DEFAULT_AI_MODE,
        "fallback_to_core": True,
        "explicit_mode_fallback_to_other_ai": False,
        "request_timeout_seconds": 20,
        "ai_daily_limit": None,
        "ai_monthly_limit": None,
    }
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        stored = {}
    if not isinstance(stored, dict):
        raise ValueError("Некорректный config/ai.json")
    config = {**defaults, **stored}
    mode = str(config.get("ai_mode") or "").upper()
    if mode not in VALID_MODES:
        raise ValueError(f"Неизвестный AI_MODE: {mode}")
    config["ai_mode"] = mode
    return config
