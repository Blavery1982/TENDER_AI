"""Этап 1: технический поиск и строгая проверка каждого требования."""
from typing import Any
from documents.procurement_audit import evaluate_candidate_model

def unique_query_terms(requirements: list[dict[str, Any]]) -> list[str]:
    markers = ("адаптер", "площадь", "перепад", "автоклав", "стерилизац", "мембран", "размер пор")
    ranked = []
    for requirement in requirements:
        text = f"{requirement['parameter']} {requirement['required_value']}".strip()
        ranked.append((sum(x in text.casefold() for x in markers) + any(c.isdigit() for c in text), text))
    return [text for _, text in sorted(ranked, reverse=True)[:6]]

def verify_candidates(requirements, candidates, target):
    results = []
    for candidate in candidates:
        checked = evaluate_candidate_model(requirements, candidate, target)
        checked["rejection_reasons"] = [f"Не соответствует параметру: {x}" for x in checked["mismatched_parameters"]]
        checked["rejection_reasons"] += [f"Не удалось подтвердить параметр: {x}" for x in checked["unconfirmed_parameters"]]
        checked["technical_sources"] = candidate.get("technical_sources", [])
        results.append(checked)
    return results


def add_justification_candidate(candidates, candidate):
    """Модель из НМЦК — дополнительный кандидат; отсутствие модели ничего не блокирует."""
    if not candidate or not candidate.get("model"):
        return list(candidates)
    key=(str(candidate.get("brand") or "").casefold(),str(candidate["model"]).casefold())
    existing={(str(x.get("brand") or "").casefold(),str(x.get("model") or "").casefold()) for x in candidates}
    return list(candidates) if key in existing else [{**candidate,"candidate_source":"price_justification"},*candidates]
