"""Локальный морфологический и смысловой анализ сохранённых закупок ЕАТ."""
from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymorphy3
from eat.normalization import normalize_lot_item
from filters.eat_filters import filter_purchase, load_config
from filters.position_kind import classify_position, ANCILLARY

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data/eat_filtered_test.json"
FILTERED = ROOT / "data/eat_filtered_semantic_test.json"
GREEN = ROOT / "data/eat_green_semantic_test.json"
FILTERED_V2 = ROOT / "data/eat_filtered_semantic_v2.json"
GREEN_V2 = ROOT / "data/eat_green_semantic_v2.json"
MORPH = pymorphy3.MorphAnalyzer()
WORD_RE = re.compile(r"[А-Яа-яЁё-]+")
ADJECTIVE_POS = {"ADJF", "ADJS", "PRTF", "PRTS"}
ACTION_RE = re.compile(r"\b(?:оказани\w*\s+услуг\w*|выполнени\w*\s+работ\w*|услуг\w+\s+по|работ\w+\s+по|монтаж\w*\s+|ремонт\w*\s+|обслуживани\w*\s+|проведени\w*\s+|разработк\w*\s+)")

# Канонические причины объединяют только явно заданные пользователем синонимы.
HARD_GROUPS = {
    "шины": ["шины", "автошины", "автомобильные шины"],
    "автомобильные колеса": ["автомобильные колеса"],
    "запчасти": ["запчасти"], "для автомобиля": ["для автомобиля"],
    "бензин": ["бензин"], "топливо": ["топливо"],
    "нефтепродукты": ["нефтепродукты"], "газ": ["газ"],
    "электроэнергия": ["электроэнергия"], "уголь": ["уголь"],
    "программное обеспечение / программы для ЭВМ": ["программное обеспечение", "программы для ЭВМ"],
    "лицензия": ["лицензия", "лицензирование"],
    "право использования": ["право использования"], "подписка": ["подписка"],
    "база данных": ["база данных"], "продукты": ["продукты", "продукты питания"],
    "овощи": ["овощи"], "овощные культуры": ["овощные культуры"],
    "корма для животных": ["корма для животных"], "комбикорм": ["комбикорм"],
    "лекарственные препараты": ["лекарственные препараты"],
    "препараты": ["препараты"],
    "анестетики": ["анестетики"],
    "наркотические средства": ["наркотические средства"],
    "психотропные вещества": ["психотропные вещества"],
    "стройка": ["стройка"], "строительные материалы": ["строительные материалы"],
    "щебень": ["щебень"], "керамзит": ["керамзит"],
    "пластиковые окна": ["пластиковые окна"], "паркет": ["паркет"],
    "лаки и краски": ["лаки и краски"],
    "этиловый спирт": ["этиловый спирт"], "охрана": ["охрана"], "ОСАГО": ["ОСАГО"],
}


@lru_cache(maxsize=16_384)
def _normal_form(word: str) -> str:
    return MORPH.parse(word)[0].normal_form.replace("ё", "е")


def _word_family(lemma: str, canonical: str, variant: str) -> bool:
    """Сопоставление целого слова/морфологической семьи, не произвольной подстроки."""
    if canonical == "продукты":
        return lemma.startswith("продукт")
    if canonical == "шины":
        return lemma in {"шина", "автошина"}
    if canonical == "паркет":
        return lemma.startswith("паркет")
    return lemma == _normal_form(variant)


def analyze_hard_exclusions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[tuple[str, int | None, str]] = []
    if isinstance(raw.get("subject"), str):
        sources.append(("subject", None, raw["subject"]))
    for index, item in enumerate(raw.get("lotItems") or []):
        if isinstance(item, dict):
            for key in ("name", "description"):
                if isinstance(item.get(key), str):
                    sources.append((f"lotItems[].{key}", index, item[key]))
    results, seen = [], set()
    for field, item_index, text in sources:
        tokens = [(m, _normal_form(m.group(0))) for m in WORD_RE.finditer(text)]
        for canonical, variants in HARD_GROUPS.items():
            for variant in variants:
                wanted = [_normal_form(w) for w in WORD_RE.findall(variant)]
                for offset in range(len(tokens) - len(wanted) + 1):
                    actual = tokens[offset:offset + len(wanted)]
                    matches = (
                        _word_family(actual[0][1], canonical, variant)
                        if len(wanted) == 1 else [x[1] for x in actual] == wanted
                    )
                    if not matches:
                        continue
                    start, end = actual[0][0].start(), actual[-1][0].end()
                    # В описании конструкция «товар для ...» обозначает область
                    # применения, а не категорию самого закупаемого товара.
                    before = text[max(0, start - 80):start].casefold()
                    if (
                        field == "lotItems[].description"
                        and not "для" in variant.casefold()
                        and re.search(r"\bдля(?:\s+[а-яё-]+){0,4}\s*$", before)
                    ):
                        continue
                    key = (field, item_index, start, canonical)
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append({
                        "hard_exclusion": canonical,
                        "actual_text": text[start:end],
                        "source_field": field,
                        "item_index": item_index,
                        "text_fragment": text[max(0, start-55):min(len(text), end+55)].replace("\n", " ").strip(),
                    })
    return results


def _position_kind(item: dict[str, Any]) -> str:
    """Классифицировать позицию, соблюдая приоритет полей спецификации."""
    return classify_position(item)["position_kind"]


def procurement_kind(raw_or_subject: Any) -> str:
    raw = raw_or_subject if isinstance(raw_or_subject, dict) else {}
    subject = raw.get("subject") if raw else raw_or_subject
    if raw:
        raw = raw.get("lot") or raw
        subject = raw.get("subject")
        item_kinds = [
            _position_kind(item) for item in (raw.get("lotItems") or [])
            if isinstance(item, dict)
        ]
        if item_kinds:
            return item_kinds[0] if len(set(item_kinds)) == 1 else "mixed"
    if not isinstance(subject, str) or not subject.strip():
        return "uncertain"
    return classify_position({"name": subject})["position_kind"]


def _actual_word(text: str, start: int, end: int) -> str:
    words = [m.group(0) for m in WORD_RE.finditer(text) if m.start() < end and m.end() > start]
    return " ".join(words) or text[start:end]


def _role(text: str, field: str, actual: str, pos: str | None, kind: str, start: int) -> str:
    lowered = text.casefold().replace("ё", "е")
    around = lowered[max(0, start - 40):start + len(actual) + 50]
    if kind in {"services", "works"}:
        return "service_or_work"
    if kind == "goods" and ANCILLARY.fullmatch(actual):
        return "delivery_obligation"
    if kind == "goods" and re.search(r"\bдля\s*$", lowered[:start]):
        return "product_property"
    if pos in ADJECTIVE_POS:
        return "product_property"
    if re.search(r"\bдля\s+(?:[а-яё-]+\s+){0,4}работ\w*\b", around):
        return "product_property"
    if ACTION_RE.search(around):
        return "service_or_work"
    if kind == "goods":
        return "product_name" if field != "lotItems[].description" else "technical_context"
    return "service_or_work"


def analyze_bad_words(raw: dict[str, Any], bad_words: list[str]) -> dict[str, Any]:
    """Служебные поля исключены: анализируются только три реальных поля."""
    kind = procurement_kind(raw)
    sources: list[tuple[str, int | None, str]] = []
    if isinstance(raw.get("subject"), str):
        sources.append(("subject", None, raw["subject"]))
    for index, item in enumerate(raw.get("lotItems") or []):
        if isinstance(item, dict):
            for key in ("name", "description"):
                if isinstance(item.get(key), str):
                    sources.append((f"lotItems[].{key}", index, item[key]))
    matches, seen = [], set()
    for field, index, text in sources:
        role_kind = _position_kind(raw["lotItems"][index]) if index is not None else kind
        lowered = text.casefold()
        for bad in dict.fromkeys(word.casefold() for word in bad_words):
            for found in re.finditer(re.escape(bad), lowered):
                unique = (field, index, found.start(), bad)
                if unique in seen:
                    continue
                seen.add(unique)
                actual = _actual_word(text, found.start(), found.end())
                token = WORD_RE.search(actual)
                pos = MORPH.parse(token.group(0))[0].tag.POS if token else None
                role = _role(text, field, actual, pos, role_kind, found.start())
                matches.append({
                    "matched_word": bad, "actual_word": actual,
                    "part_of_speech": pos, "source_field": field,
                    "item_index": index,
                    "text_fragment": text[max(0, found.start()-55):min(len(text), found.end()+55)].replace("\n", " ").strip(),
                    "semantic_role": role,
                    "affects_decision": role == "service_or_work",
                })
    return {"procurement_kind": kind, "matches": matches,
            "rejecting_matches": [m for m in matches if m["affects_decision"]]}


def _filter(raw: dict[str, Any], title: str | None, config: dict[str, Any]) -> dict[str, Any]:
    base = filter_purchase(raw, title, config)
    if base["filter_result"] == "confidential_locked":
        return {**base, "procurement_kind": "confidential_locked", "bad_word_matches": []}
    analysis = analyze_bad_words(raw, config["bad_words"])
    reasons = [r for r in base["rejection_reasons"] if not r.startswith("bad_word: ")]
    kind = analysis["procurement_kind"]
    if kind in {"services", "works", "mixed", "uncertain"}:
        reasons.append(f"procurement_kind_{kind}")
    if kind == "goods":
        for match in analysis["rejecting_matches"]:
            reason = f"bad_word: {match['matched_word']}"
            if reason not in reasons:
                reasons.append(reason)
    manual = {"price_not_determined", "law_not_determined", "region_not_determined", "items_count_not_determined", "procurement_kind_mixed", "procurement_kind_uncertain"}
    result = "rejected" if any(r not in manual for r in reasons) else "manual_check" if reasons else "passed"
    words = list(dict.fromkeys(m["matched_word"] for m in analysis["matches"]))
    checks = dict(base["rule_checks"])
    checks["no_bad_words"] = not analysis["rejecting_matches"]
    return {**base, "filter_result": result, "rejection_reasons": reasons,
            "matched_bad_words": words, "rule_checks": checks,
            "procurement_kind": kind, "bad_word_matches": analysis["matches"]}


def _filter_v2(raw: dict[str, Any], title: str | None, config: dict[str, Any]) -> dict[str, Any]:
    """Приоритет жёстких товарных категорий над контекстными совпадениями."""
    base = filter_purchase(raw, title, config)
    if base["filter_result"] == "confidential_locked":
        return {
            **base, "procurement_kind": "confidential_locked",
            "hard_exclusion_matches": [], "contextual_exclusion_matches": [],
        }
    contextual = analyze_bad_words(raw, config["contextual_exclusions"])
    hard_matches = analyze_hard_exclusions(raw)
    reasons = [r for r in base["rejection_reasons"] if not r.startswith("bad_word: ")]
    kind = contextual["procurement_kind"]
    if kind in {"services", "works", "mixed", "uncertain"}:
        reasons.append(f"procurement_kind_{kind}")
    for match in hard_matches:
        reason = f"hard_exclusion: {match['hard_exclusion']}"
        if reason not in reasons:
            reasons.append(reason)
    if kind == "goods":
        for match in contextual["rejecting_matches"]:
            reason = f"contextual_exclusion: {match['matched_word']}"
            if reason not in reasons:
                reasons.append(reason)
    manual = {"price_not_determined", "law_not_determined", "region_not_determined", "items_count_not_determined", "procurement_kind_mixed", "procurement_kind_uncertain"}
    result = "rejected" if any(r not in manual for r in reasons) else "manual_check" if reasons else "passed"
    checks = dict(base["rule_checks"])
    checks["no_hard_exclusions"] = not hard_matches
    checks["no_decisive_contextual_exclusions"] = not contextual["rejecting_matches"]
    checks.pop("no_bad_words", None)
    return {
        **base, "filter_result": result, "rejection_reasons": reasons,
        "matched_bad_words": list(dict.fromkeys(
            [m["hard_exclusion"] for m in hard_matches]
            + [m["matched_word"] for m in contextual["matches"]]
        )),
        "rule_checks": checks, "procurement_kind": kind,
        "hard_exclusion_matches": hard_matches,
        "contextual_exclusion_matches": contextual["matches"],
    }


def filter_purchase_v2(raw: dict[str, Any], title: str | None,
                       config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Публичная production-oriented точка актуального semantic v2 фильтра."""
    normalized = {**raw, **(raw.get("lot") or {})}
    result = _filter_v2(normalized, title, config or load_config())
    result["position_kinds"] = [classify_position(item) for item in normalized.get("lotItems") or []
                                if isinstance(item, dict)]
    return result


def recalculate_saved_purchases_v2() -> dict[str, Any]:
    source, config = json.loads(SOURCE.read_text(encoding="utf-8")), load_config()
    purchases = []
    for old in source["purchases"]:
        raw = old["raw"]
        result = _filter_v2(raw, old.get("purchaseTypeTitle"), config)
        normalized = dict(old.get("normalized") or {})
        normalized["lot_items"] = [normalize_lot_item(i) for i in (raw.get("lotItems") or []) if isinstance(i, dict)]
        purchases.append({"raw_id": old.get("raw_id"), "normalized": normalized, **result, "raw": raw})
    new_counts = Counter(p["filter_result"] for p in purchases)
    hard_purchases = [p for p in purchases if p.get("hard_exclusion_matches")]
    contextual_purchases = [p for p in purchases if p.get("contextual_exclusion_matches")]
    metadata = {
        "source_file": str(SOURCE), "records": len(purchases),
        "hard_exclusion_purchase_count": len(hard_purchases),
        "hard_exclusion_match_count": sum(len(p["hard_exclusion_matches"]) for p in hard_purchases),
        "contextual_exclusion_purchase_count": len(contextual_purchases),
        "contextual_exclusion_match_count": sum(len(p["contextual_exclusion_matches"]) for p in contextual_purchases),
        "results": dict(new_counts),
        "analysis_scope": ["subject", "lotItems[].name", "lotItems[].description"],
    }
    FILTERED_V2.write_text(json.dumps({"metadata": metadata, "purchases": purchases}, ensure_ascii=False, indent=2), encoding="utf-8")
    green = [p for p in purchases if p["filter_result"] == "passed"]
    GREEN_V2.write_text(json.dumps({"metadata": {**metadata, "passed_records": len(green)}, "purchases": green}, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def recalculate_saved_purchases() -> dict[str, Any]:
    source, config = json.loads(SOURCE.read_text(encoding="utf-8")), load_config()
    purchases, changed = [], 0
    for old in source["purchases"]:
        raw = old["raw"]
        result = _filter(raw, old.get("purchaseTypeTitle"), config)
        normalized = dict(old.get("normalized") or {})
        normalized["lot_items"] = [normalize_lot_item(i) for i in (raw.get("lotItems") or []) if isinstance(i, dict)]
        new = {"raw_id": old.get("raw_id"), "normalized": normalized, **result, "raw": raw}
        purchases.append(new)
        changed += new["filter_result"] != old["filter_result"]
    kinds = Counter(p["procurement_kind"] for p in purchases)
    old_counts = Counter(p["filter_result"] for p in source["purchases"])
    new_counts = Counter(p["filter_result"] for p in purchases)
    pairs = [(o, n) for o, n in zip(source["purchases"], purchases) if o["filter_result"] == "rejected" and any(r.startswith("bad_word: ") for r in o["rejection_reasons"])]
    goods_pairs = [(o, n) for o, n in pairs if n["procurement_kind"] == "goods"]
    metadata = {
        "source_file": str(SOURCE), "records": len(purchases),
        "procurement_kind_counts": dict(kinds), "old_results": dict(old_counts),
        "new_results": dict(new_counts), "changed_decisions": changed,
        "previously_rejected_by_bad_words": len(pairs),
        "previously_bad_word_rejected_now_goods": len(goods_pairs),
        "now_goods_results": dict(Counter(n["filter_result"] for _, n in goods_pairs)),
        "analysis_scope": ["subject", "lotItems[].name", "lotItems[].description"],
    }
    FILTERED.write_text(json.dumps({"metadata": metadata, "purchases": purchases}, ensure_ascii=False, indent=2), encoding="utf-8")
    green = [p for p in purchases if p["filter_result"] == "passed"]
    GREEN.write_text(json.dumps({"metadata": {**metadata, "passed_records": len(green)}, "purchases": green}, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


if __name__ == "__main__":
    print(json.dumps(recalculate_saved_purchases_v2(), ensure_ascii=False, indent=2))
