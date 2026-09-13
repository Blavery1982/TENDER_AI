"""Проверка производства и реальной доступности модели в Российской Федерации."""

RUSSIAN_PARKER_SOURCES = [
    "https://ru-parker.com/zhftat-v/",
    "https://ei.spb.ru/product/413632",
    "https://parker-filter.ru/index.php?id=49864",
]
DISCONTINUED_SIGNAL = "https://www.radwell.com/Buy/PARKER/DOMNICK%20HUNTER/ZHFT-DIV-AT"

def apply_market_eligibility(candidate):
    model = candidate.get("model", "")
    if "HIGH FLOW TETPOR II" in model:
        candidate.update({
            "production_status": "production_not_confirmed",
            "production_status_ru": "Актуальность производства не подтверждена",
            "production_status_sources": candidate.get("technical_sources", []) + [DISCONTINUED_SIGNAL],
            "production_status_note": "Серия присутствует в материалах Parker, но независимый источник помечает базовый ZHFT/AT как discontinued; официальный статус точного исполнения не найден.",
            "russia_availability": "available_to_order",
            "russia_availability_ru": "Доступно к заказу в России",
            "russia_availability_sources": RUSSIAN_PARKER_SOURCES,
            "russia_availability_note": "Найдены российские страницы с заказом по запросу и доставкой по РФ; числовая цена не опубликована.",
        })
    elif candidate.get("brand") in {"Pall", "Sartorius"}:
        candidate.update({
            "production_status": "in_production",
            "production_status_ru": "Модель представлена в актуальном каталоге производителя",
            "production_status_sources": candidate.get("technical_sources", []),
            "production_status_note": "Найдена действующая страница модели производителя; сведений о снятии с производства не обнаружено.",
            "russia_availability": "not_confirmed",
            "russia_availability_ru": "Доступность для поставки в РФ не подтверждена",
            "russia_availability_sources": [],
            "russia_availability_note": "Подтверждающая страница российского рынка для точной модели не найдена.",
        })
    else:
        candidate.update({
            "production_status": "production_not_confirmed",
            "production_status_ru": "Актуальность производства не подтверждена",
            "production_status_sources": [],
            "production_status_note": "Нет данных.",
            "russia_availability": "not_confirmed",
            "russia_availability_ru": "Доступность для поставки в РФ не подтверждена",
            "russia_availability_sources": [],
            "russia_availability_note": "Нет данных.",
        })
    if candidate["production_status"] != "in_production":
        candidate["rejection_reasons"].append("Актуальность производства не подтверждена")
    if candidate["russia_availability"] not in {"available", "available_to_order"}:
        candidate["rejection_reasons"].append("Доступность для поставки в РФ не подтверждена")
    return candidate

def market_eligible(candidate):
    return (candidate.get("production_status") == "in_production"
            and candidate.get("russia_availability") in {"available", "available_to_order"})
