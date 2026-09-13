"""Read-only runtime access to explicitly approved CORE knowledge."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
APPROVED_RULES_PATH = ROOT / "approved_rules.json"


def load_approved_rules(path: Path = APPROVED_RULES_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("rules"), list):
        raise ValueError("Некорректный реестр CORE knowledge")
    return deepcopy(value)


def build_rule_candidate(*, skill: str, description: str,
                         evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return a proposal only. This function deliberately performs no writes."""
    return {
        "status": "CORE_RULE_CANDIDATE",
        "skill": skill,
        "description": description,
        "evidence": deepcopy(evidence or []),
        "approved": False,
    }
