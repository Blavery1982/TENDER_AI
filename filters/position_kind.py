"""Детерминированное определение предмета позиции до поиска моделей и цен."""
from __future__ import annotations

import re
import json
from pathlib import Path

OFFICIAL_KINDS = json.loads((Path(__file__).resolve().parent.parent / "config/eat_position_categories.json").read_text(encoding="utf-8"))
SUPPLY = re.compile(r"\b(?:поставк\w*|приобретен\w*|закупк\w*\s+товар\w*)\b", re.I)
SERVICE = re.compile(r"\b(?:услуг\w*|(?:техническ\w*\s+)?обслуживани\w*|диагностик\w*|обучени\w*|страховани\w*|экспертиз\w*|тестировани\w*)\b", re.I)
WORK = re.compile(r"\b(?:выполнени\w*\s+работ\w*|работ\w*\s+по|(?:пуско[- ]?наладочн\w*\s+)?работ\w*|ремонт(?:а|у|ом|е|ы|ов)?|восстановлени\w*|монтаж\w*|демонтаж\w*|строительств\w*|изготовлени\w*)\b", re.I)
PLACEHOLDER = re.compile(
    r"^(?:товар|поставка|закупка|работы|услуги|требуется|не определено|в соответствии с .+|согласно .+|см\.?\s+.+)\.?$", re.I
)
ANCILLARY = re.compile(r"\b(?:монтаж\w*|установк\w*|сборк\w*|погрузк\w*|разгрузк\w*)\b", re.I)


def classify_position(item: dict) -> dict:
    """Категория ЕАТ приоритетна; документы и условия договора не классифицируют предмет."""
    eat = item.get("eat") or {}
    categories = [("eat.title", eat.get("title") if isinstance(eat, dict) else None),
                  ("eatTitle", item.get("eatTitle")), ("eat_title", item.get("eat_title"))]
    for field, value in categories:
        if isinstance(value, str) and value.strip().upper() in OFFICIAL_KINDS:
            return {"position_kind": OFFICIAL_KINDS[value.strip().upper()],
                    "position_kind_source": field, "position_kind_evidence": value,
                    "official_eat_category": value.strip().upper()}
    # Уже установленный нетоварный тип нельзя снять обнаружением идентификатора.
    if item.get("position_kind") in {"works", "services", "uncertain"}:
        return {k: item.get(k) for k in ("position_kind", "position_kind_source", "position_kind_evidence", "official_eat_category")}
    fields = ("name", "item_name", "description", "offerDescription", "offer_description", "eatTitle")
    concrete = []
    for field in fields:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip() or value.strip() == "-":
            continue
        text = value.strip().replace("ё", "е")
        # Отдельные «работы/услуги» — предмет; прочие заглушки не дают допуска.
        if text.casefold() in {"работы", "услуги"}:
            kind = "works" if text.casefold() == "работы" else "services"
        elif PLACEHOLDER.fullmatch(text):
            continue
        else:
            supply, service, work = SUPPLY.search(text), SERVICE.search(text), WORK.search(text)
            action = min([m for m in (service, work) if m], key=lambda m: m.start(), default=None)
            if supply and (not action or supply.start() < action.start()):
                kind = "goods"
            elif action:
                # «Товар для ремонта» описывает назначение товара, а не закупку ремонта.
                before = text[:action.start()]
                if re.search(r"\bдля\s*$", before, re.I):
                    concrete.append((field, value))
                    continue
                if action is work and re.search(
                    r"\bдля\s+(?:[а-яё-]+\s+){0,4}работ\w*\b", text, re.I
                ):
                    concrete.append((field, value))
                    continue
                if concrete and action is work and ANCILLARY.fullmatch(action.group(0)):
                    # Монтаж при именованном товаре может быть обязанностью поставки.
                    continue
                kind = "services" if action is service else "works"
            else:
                concrete.append((field, value))
                continue
        return {"position_kind": kind, "position_kind_source": field,
                "position_kind_evidence": value, "official_eat_category": None}
    if concrete:
        field, value = concrete[0]
        kind = "goods"
    else:
        field, value = None, None
        kind = "goods" if item.get("position_kind") == "goods" else "uncertain"
    return {"position_kind": kind, "position_kind_source": field,
            "position_kind_evidence": value, "official_eat_category": None}


def goods_position(item: dict, resolved: dict | None = None) -> bool:
    """Проверять исходную позицию; сохранённая модель не переопределяет её тип."""
    if resolved and resolved.get("position_kind") in {"services", "works", "uncertain"}:
        return False
    if (resolved and resolved.get("position_kind") == "goods" and not item.get("position_kind")
            and not any(item.get(k) for k in ("name", "item_name", "description", "eat", "eatTitle", "eat_title"))):
        # Сокращённый вход с одним SKU использует уже классифицированную исходную позицию.
        return True
    return classify_position(item if item else resolved or {})["position_kind"] == "goods"


def blocked_position(item: dict) -> dict:
    decision = classify_position(item)
    kind = decision["position_kind"]
    reason = "Тип позиции не подтверждён; нужна ручная проверка" if kind == "uncertain" else "Самостоятельная закупка услуг/работ; оборудование является объектом услуги/работы"
    return {**decision, "model_search_mode": "POSITION_KIND_REVIEW_REQUIRED" if kind == "uncertain" else "NOT_A_GOODS_POSITION",
            "original_model": None, "customer_required_model": None, "pricing_reference_model": None,
            "model_mode_reason": reason, "model_discovery_allowed": False, "price_search_allowed": False,
            "requirements": [], "model_evidence": [], "price_model_evidence": [],
            "model_source": "NOT_APPLICABLE", "source_resolution_version": 5,
            "source_warnings": [], "source_conflicts": [], "price_justification_model": None,
            "document_model_candidates": [], "product_identifiers": [],
            "equipment_identity_role": "service_or_work_object" if kind != "uncertain" else "uncertain"}


def ancillary_conditions(item: dict) -> list[str]:
    if not goods_position(item):
        return []
    text = "\n".join(str(item.get(k) or "") for k in ("name", "description"))
    return list(dict.fromkeys(m.group(0) for m in ANCILLARY.finditer(text)))
