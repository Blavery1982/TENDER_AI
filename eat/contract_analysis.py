"""Отдельный тест документов для уже отобранных закупок ЕАТ."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Response, sync_playwright
from eat.browser_policy import open_authorized_eat_browser
from pypdf import PdfReader

from filters.eat_filters import load_config
from documents.tender_archive import TENDERS_ROOT, tender_folder

ROOT = Path(__file__).resolve().parent.parent
GREEN_PATH = ROOT / "data/eat_green_semantic_v2.json"
OUTPUT_PATH = ROOT / "data/eat_contract_test.json"
# Единый архив: отдельная папка на каждый тендер.
CONTRACTS_ROOT = TENDERS_ROOT
DOC_WORDS = ("договор", "контракт", "техническ", "задани", "приложен", "спецификац")
SUPPORTED = {".pdf", ".docx", ".doc", ".docm", ".xlsx", ".xls", ".xlsm", ".csv", ".rtf", ".txt", ".odt", ".ods", ".zip", ".rar", ".7z", ".png", ".jpg", ".jpeg"}
SECRET_KEYS = ("authorization", "cookie", "token", "csrf", "password")
TYPE_MAP = {
    "сборк": "assembly", "монтаж": "installation", "пусконалад": "commissioning",
    "пуско-налад": "commissioning", "установк": "installation", "подключ": "connection",
    "разгруз": "unloading", "погруз": "loading", "подъем": "floor_delivery",
    "подъём": "floor_delivery", "этаж": "floor_delivery", "занос": "carrying_inside",
    "обучени": "training", "демонтаж": "dismantling", "утилизац": "disposal",
    "вывоз": "packaging_removal", "доставк": "other",
}
ACCESS_PATTERNS = {
    "access_pass": re.compile(r"пропускн\w*\s+режим|(?:оформ|заказ|нужен|требуется)\w*\s+пропуск", re.I),
    "advance_personal_data": re.compile(r"(?:заранее|за\s+\d+\s+(?:рабоч\w*\s+)?дн\w*).{0,100}(?:ФИО|паспортн\w*\s+данн)", re.I | re.S),
    "passport_details": re.compile(r"паспортн\w*\s+данн\w*.{0,80}(?:водител|грузчик|представител)", re.I | re.S),
    "russian_access": re.compile(r"паспорт\w*\s+РФ|гражданств\w*\s+(?:РФ|Российск\w*\s+Федерац)", re.I),
    "vehicle_details": re.compile(r"\bмарк(?:а|у|и|е|ой)\s+(?:автомобил\w*|транспортн\w*\s+средств\w*)|государственн\w*\s+номер|госномер|(?:данн\w*|сведени\w*)\s+(?:об\s+)?(?:автомобил\w*|транспортн\w*\s+средств\w*)", re.I),
    "entry_time_restriction": re.compile(r"(?:въезд|доступ).{0,100}(?:с\s+\d{1,2}[.:]\d{2}|до\s+\d{1,2}[.:]\d{2}|рабоч\w*\s+врем)", re.I | re.S),
    "vehicle_restriction": re.compile(r"(?:габарит|грузоподъ[её]мност|тип)\w*.{0,70}(?:автомоб|транспорт)", re.I | re.S),
    "restricted_site": re.compile(r"(?:режимн|закрыт)\w*\s+(?:объект|территори)", re.I),
    "customer_escort": re.compile(r"сопровожд\w*.{0,80}представител\w*\s+заказчик", re.I | re.S),
    "paid_access": re.compile(r"платн\w*\s+(?:въезд|пропуск)|(?:оплат|стоимост)\w*\s+(?:въезд|пропуск)", re.I),
}
OBLIGATION_RE = re.compile(
    r"(?:поставщик|исполнитель|подрядчик).{0,180}(?:обязан|должен|осуществл|выполня|"
    r"производ|обеспеч)|(?:обязан|должен|осуществл|выполня|производ|обеспеч).{0,180}"
    r"(?:поставщик|исполнитель|подрядчик)|(?:силами|за\s+сч[её]т).{0,60}"
    r"(?:поставщик|исполнитель)|(?:включает|включен|входит).{0,100}(?:стоимост|цен|расход|работ)",
    re.I | re.S,
)


def _file_url(purchase_id: str, document_type: Any, file_id: str) -> str:
    return (
        "https://agregatoreat.ru/integration/ecom/rest/api/file/1/300916501/"
        f"{purchase_id}/{document_type}/{file_id}"
    )


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._() -]+", "_", name).strip(" .")
    return cleaned[:180] or "document"


def _is_secret_key(key: Any) -> bool:
    return any(x in str(key).casefold() for x in SECRET_KEYS)


def _documents(value: Any, source_url: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        lowered = {str(k).casefold(): v for k, v in value.items()}
        name = next((lowered[k] for k in ("filename", "file_name", "name", "title", "documentname") if isinstance(lowered.get(k), str)), None)
        file_id = next((lowered[k] for k in ("fileid", "file_id", "documentid", "id") if lowered.get(k) is not None), None)
        url = next((lowered[k] for k in ("downloadurl", "download_url", "fileurl", "url", "href") if isinstance(lowered.get(k), str)), None)
        if name and (file_id or url) and (any(w in name.casefold() for w in DOC_WORDS) or Path(name).suffix.casefold() in SUPPORTED):
            found.append({"file_name": name, "document_type": lowered.get("type") or lowered.get("documenttype"), "file_id": str(file_id) if file_id else None, "download_url": urljoin(source_url, url) if url else None})
        for key, item in value.items():
            if not _is_secret_key(key):
                found.extend(_documents(item, source_url))
    elif isinstance(value, list):
        for item in value:
            found.extend(_documents(item, source_url))
    return found


def _extract_text(path: Path) -> tuple[str | None, str]:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".pdf":
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages), "analyzed"
        if suffix in {".doc", ".docx"}:
            result = subprocess.run(["/usr/bin/textutil", "-convert", "txt", "-stdout", str(path)], capture_output=True, check=True)
            return result.stdout.decode("utf-8", errors="replace"), "analyzed"
        return None, "unsupported"
    except Exception:
        return None, "parse_failed"


def _fragment(text: str, start: int, end: int) -> str:
    return re.sub(r"\s+", " ", text[max(0, start-100):min(len(text), end+140)]).strip()


def analyze_text(text: str, source: str, config: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    lowered = text.casefold()
    hard = []
    for root, flag in (("субсид", "subsidy"), ("оборон", "defense")):
        if root in lowered and flag not in hard:
            hard.append(flag)
    extras, seen = [], set()
    for root in config["contract_analysis"]["extra_requirement_roots"]:
        for match in re.finditer(re.escape(root), lowered):
            context = _fragment(text, match.start(), match.end())
            local = lowered[max(0, match.start()-55):min(len(lowered), match.end()+100)]
            # Характеристики и комплект документации не являются работами поставщика.
            if root in {"установк", "подключ"} and re.search(
                r"руководств\w*.{0,30}установк|тип\s+подключ|(?:кабел|шнур)\w*.{0,25}подключ",
                local,
            ):
                continue
            # Ответственность за повреждение не устанавливает обязанность разгрузки.
            if root in {"погруз", "разгруз"} and re.search(r"получивш\w*.{0,90}поврежден", context, re.I):
                continue
            if re.search(
                r"(?:не\s+требу\w*|не\s+предусмотр\w*|не\s+осуществл\w*|"
                r"не\s+вход\w*|не\s+производ\w*|не\s+выполня\w*|"
                r"обязанност\w*\s+не\s+возлага\w*)",
                local,
            ):
                continue
            if root in {"подъем", "подъём", "этаж"} and not re.search(
                r"подъ[её]м\w*(?:\s+\w+){0,3}\s+этаж\w*|этаж\w*.{0,35}подъ[её]м",
                local,
            ):
                continue
            # Служебное перечисление «товар (работа, услуга)» не является обязанностью.
            confidence = "high" if (
                OBLIGATION_RE.search(context)
                or re.search(r"своими\s+силами", context, re.I)
                or re.search(r"(?:затрат|расход)\w*\s+поставщик\w*", context, re.I)
                or re.search(r"\b(?:требу\w*|необходим\w*|обязател\w*)", context, re.I)
            ) else "medium"
            kind = TYPE_MAP[root]
            key = (kind, context)
            if key in seen:
                continue
            seen.add(key)
            extras.append({"type": kind, "matched_root": root, "matched_text": context, "source_document": source, "confidence": confidence})
    for match in re.finditer(r"\bПНР\b", text, re.I):
        context = _fragment(text, match.start(), match.end())
        if not OBLIGATION_RE.search(context) or re.search(r"не\s+(?:требу|предусмотр)\w*", context, re.I):
            continue
        extras.append({"type": "commissioning", "matched_text": context,
                       "source_document": source, "confidence": "high"})
    return hard, extras


def analyze_access_conditions(text: str, source: str) -> list[dict[str, Any]]:
    evidence = []
    for kind, pattern in ACCESS_PATTERNS.items():
        for match in pattern.finditer(text):
            local = text[max(0, match.start()-60):min(len(text), match.end()+100)]
            if re.search(r"не\s+(?:требу\w*|предусмотр\w*|осуществл\w*|нуж\w*)", local, re.I):
                continue
            evidence.append({"type": kind, "matched_text": _fragment(text, match.start(), match.end()), "source_document": source, "confidence": "high"})
    return evidence


def contract_summary(extras: list[dict[str, Any]], *, analyzed: bool = True) -> str:
    """Краткое резюме отдельных обязанностей, без оценки денежных расходов."""
    labels = {
        "assembly": "Сборка на месте", "installation": "Монтаж",
        "commissioning": "ПНР", "connection": "Подключение",
        "unloading": "Разгрузка", "loading": "Погрузка",
        "floor_delivery": "Подъём на этаж", "carrying_inside": "Занос",
        "training": "Обучение", "dismantling": "Демонтаж",
        "disposal": "Вывоз старого оборудования",
        "packaging_removal": "Вывоз упаковки", "access_pass": "Нужен пропуск",
        "paid_access": "Платный въезд/пропуск", "advance_personal_data": "Передать ФИО и паспортные данные заранее",
        "passport_details": "Паспортные данные", "russian_access": "Допуск с паспортом РФ",
        "vehicle_details": "Данные автомобиля", "entry_time_restriction": "Ограничения времени въезда",
        "vehicle_restriction": "Ограничения транспорта", "restricted_site": "Режимный объект",
        "customer_escort": "Сопровождение заказчиком",
    }
    result = []
    for extra in extras:
        kind, text = extra.get('type'), extra.get('matched_text') or ''
        if kind not in labels:
            continue
        if extra.get('confidence') != 'high':
            if kind == 'unloading' and re.search(r'до\s+места\s+назначения\s+и\s+разгруз', text, re.I):
                label = 'Упомянута разгрузка на складе заказчика; кто выполняет — не уточнено'
            else:
                continue
        else:
            label = labels[kind]
            if kind == 'floor_delivery':
                floor = re.search(r'подъ[её]м\w*\s+на\s+(\d+)\s*(?:-?[а-я]+\s+)?этаж', text, re.I)
                if floor:
                    label = f'Подъём на {floor[1]} этаж'
        if (str(extra.get('source_document') or '').startswith('Позиция ЕАТ')
                and text and len(text) < 80 and text.casefold() not in label.casefold()):
            label = f'{label} ({text})'
        if label not in result:
            result.append(label)
    if not analyzed:
        result.append('Анализ документов не завершён')
    return ', '.join(result) if result else 'Нет специальных условий'


def _note(extras: list[dict[str, Any]]) -> str:
    labels = {"assembly":"Сборка", "installation":"Монтаж/установка", "commissioning":"Пусконаладка", "connection":"Подключение", "unloading":"Разгрузка", "loading":"Погрузка", "floor_delivery":"Подъём на этаж", "carrying_inside":"Занос", "training":"Обучение", "dismantling":"Демонтаж", "disposal":"Утилизация", "packaging_removal":"Вывоз", "other":"Доставка", "access_pass":"Пропуск", "advance_personal_data":"Предварительная передача данных", "passport_details":"Паспортные данные", "russian_access":"Допуск только с паспортом РФ", "vehicle_details":"Данные автомобиля", "entry_time_restriction":"Ограничение времени въезда", "vehicle_restriction":"Ограничение транспорта", "restricted_site":"Режимный объект", "customer_escort":"Сопровождение заказчиком"}
    labels.update(delivery="Доставка", paid_access="Платный въезд/пропуск", insurance="Страхование")
    return "; ".join(dict.fromkeys(labels[x["type"]] for x in extras))


def _special_conditions(hard: list[str], extras: list[dict[str, Any]]) -> str:
    conditions: list[str] = []
    if "subsidy" in hard:
        conditions.append("СУБСИДИИ")
    if "defense" in hard:
        conditions.append("ОБОРОННЫЕ УСЛОВИЯ")
    labels = {
        "delivery": "Требуется доставка",
        "paid_access": "Требуется платный въезд/пропуск",
        "insurance": "Предусмотрены расходы на страхование",
        "assembly": "Требуется сборка товара на месте",
        "commissioning": "Требуется пусконаладка",
        "connection": "Требуется подключение",
        "unloading": "Требуется разгрузка силами поставщика",
        "loading": "Требуется погрузка",
        "floor_delivery": "Требуется подъём на этаж",
        "carrying_inside": "Требуется занос в помещение",
        "training": "Требуется обучение",
        "dismantling": "Требуется демонтаж",
        "disposal": "Требуется утилизация",
        "packaging_removal": "Требуется вывоз упаковки",
        "access_pass": "Требуется оформление пропуска",
        "advance_personal_data": "Нужно заранее передать ФИО и паспортные данные",
        "passport_details": "Требуются паспортные данные водителей/грузчиков",
        "russian_access": "Для допуска требуется паспорт РФ или гражданство РФ",
        "vehicle_details": "Нужно заранее передать марку и госномер автомобиля",
        "entry_time_restriction": "Ограничено время въезда",
        "vehicle_restriction": "Есть ограничения по транспорту",
        "restricted_site": "Доставка на режимный или закрытый объект",
        "customer_escort": "Требуется сопровождение представителем заказчика",
    }
    for extra in extras:
        kind = extra["type"]
        if kind == "other":
            continue
        if kind == "installation":
            label = "Требуется установка" if extra.get("matched_root") == "установк" else "Требуется монтаж"
        else:
            label = labels[kind]
        if (str(extra.get("source_document") or "").startswith("Позиция ЕАТ")
                and extra.get("matched_text")
                and str(extra["matched_text"]).casefold() not in label.casefold()):
            label = f"{label} ({extra['matched_text']})"
        if label not in conditions:
            conditions.append(label)
    return "; ".join(conditions)


def run_contract_test() -> int:
    purchases = json.loads(GREEN_PATH.read_text(encoding="utf-8"))["purchases"]
    config = load_config()
    results = []
    with sync_playwright() as pw:
        session = open_authorized_eat_browser(pw)
        context,page=session.context,session.page
        for purchase in purchases:
            n = purchase["normalized"]
            number, purchase_id = n["number"], n["id"]
            captured: list[dict[str, Any]] = []
            direct_urls: list[str] = []
            def on_response(response: Response) -> None:
                ctype = response.headers.get("content-type", "").casefold()
                disposition = response.headers.get("content-disposition", "")
                if any(x in ctype for x in ("json", "pdf", "word", "octet-stream")):
                    if "json" in ctype:
                        try: captured.extend(_documents(response.json(), response.url))
                        except Exception: pass
                    elif disposition or Path(urlsplit(response.url).path).suffix.casefold() in SUPPORTED:
                        direct_urls.append(response.url)
            page.on("response", on_response)
            card_url = f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info"
            page.goto(card_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(6_000)
            for anchor in page.locator("a[href]").all():
                try:
                    href, label = anchor.get_attribute("href"), anchor.inner_text(timeout=500)
                    if href and (any(w in label.casefold() for w in DOC_WORDS) or Path(urlsplit(href).path).suffix.casefold() in SUPPORTED):
                        captured.append({"file_name": label.strip() or Path(urlsplit(href).path).name, "document_type": None, "file_id": None, "download_url": urljoin(page.url, href)})
                except Exception: pass
            page.remove_listener("response", on_response)
            unique: dict[str, dict[str, Any]] = {}
            for doc in captured:
                key = doc.get("file_id") or doc.get("download_url") or doc["file_name"]
                unique[str(key)] = doc
            folder = tender_folder(purchase_id, tender_number=number, root=CONTRACTS_ROOT)
            files, all_hard, all_extras, statuses = [], [], [], []
            for doc in unique.values():
                if not doc.get("download_url") and doc.get("file_id") and doc.get("document_type") is not None:
                    doc["download_url"] = _file_url(purchase_id, doc["document_type"], doc["file_id"])
                url = doc.get("download_url")
                if not url:
                    files.append({**doc, "local_path": None, "document_parse_status": "unsupported"})
                    statuses.append("unsupported")
                    continue
                response = context.request.get(url)
                if not response.ok:
                    files.append({**doc, "local_path": None, "document_parse_status": "parse_failed"})
                    statuses.append("parse_failed")
                    continue
                name = _safe_name(doc["file_name"])
                suffix = Path(name).suffix.casefold()
                if not suffix:
                    ctype = response.headers.get("content-type", "").casefold()
                    suffix = ".pdf" if "pdf" in ctype else ".docx" if "wordprocessingml" in ctype else ""
                    name += suffix
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / name
                body = response.body()
                if not path.exists() or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(body).digest():
                    path.write_bytes(body)
                text, status = _extract_text(path)
                hard, extras = analyze_text(text or "", name, config) if text else ([], [])
                if text:
                    extras.extend(analyze_access_conditions(text, name))
                all_hard.extend(hard); all_extras.extend(extras); statuses.append(status)
                files.append({**doc, "local_path": str(path), "document_parse_status": status})
            available = bool(files)
            overall = "no_contract" if not available else "parse_failed" if "parse_failed" in statuses else "unsupported" if statuses and all(x == "unsupported" for x in statuses) else "analyzed"
            decisive_extras = [x for x in all_extras if x["confidence"] == "high" and x["type"] != "other"]
            extra_required = bool(decisive_extras)
            flags = list(dict.fromkeys(all_hard))
            special = _special_conditions(flags, decisive_extras)
            results.append({"tradeNumber": number, "subject": n["name"], "contract_available": available, "contract_files": files, "contract_analysis_status": overall, "hard_contract_flags": flags, "extra_requirements": all_extras, "extra_costs_required": extra_required, "extra_costs_note": _note(decisive_extras), "special_conditions": special, "special_conditions_short": special, "special_conditions_evidence": decisive_extras, "extra_costs_estimate": None, "current_filter_result": purchase["filter_result"], "hypothetical_result": purchase["filter_result"]})
            print(f"{number}: документов {len(files)}, статус {overall}")
        OUTPUT_PATH.write_text(json.dumps({"purchases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        session.close()
    return 0


def complete_saved_contract_test() -> dict[str, Any]:
    """Скачать уже найденные метаданные, не открывая браузер повторно."""
    report = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    green = json.loads(GREEN_PATH.read_text(encoding="utf-8"))["purchases"]
    ids = {p["normalized"]["number"]: p["normalized"]["id"] for p in green}
    config = load_config()
    for purchase in report["purchases"]:
        number, purchase_id = purchase["tradeNumber"], ids[purchase["tradeNumber"]]
        all_hard, all_extras, statuses = [], [], []
        for doc in purchase["contract_files"]:
            if not doc.get("download_url") and doc.get("file_id") and doc.get("document_type") is not None:
                doc["download_url"] = _file_url(purchase_id, doc["document_type"], doc["file_id"])
            url = doc.get("download_url")
            if not url:
                doc["document_parse_status"] = "unsupported"; statuses.append("unsupported"); continue
            folder = tender_folder(purchase_id, tender_number=number, root=CONTRACTS_ROOT); folder.mkdir(parents=True, exist_ok=True)
            path = folder / _safe_name(doc["file_name"])
            try:
                if not path.exists():
                    with urllib.request.urlopen(url, timeout=60) as response:
                        path.write_bytes(response.read())
                doc["local_path"] = str(path)
                text, status = _extract_text(path)
            except Exception:
                text, status = None, "parse_failed"
            doc["document_parse_status"] = status; statuses.append(status)
            if text:
                hard, extras = analyze_text(text, doc["file_name"], config)
                extras.extend(analyze_access_conditions(text, doc["file_name"]))
                all_hard.extend(hard); all_extras.extend(extras)
        purchase["contract_available"] = bool(purchase["contract_files"])
        purchase["contract_analysis_status"] = (
            "no_contract" if not purchase["contract_files"] else
            "analyzed" if "analyzed" in statuses else
            "parse_failed" if "parse_failed" in statuses else "unsupported"
        )
        purchase["hard_contract_flags"] = list(dict.fromkeys(all_hard))
        purchase["extra_requirements"] = all_extras
        decisive = [x for x in all_extras if x["confidence"] == "high" and x["type"] != "other"]
        purchase["extra_costs_required"] = bool(decisive)
        purchase["extra_costs_note"] = _note(decisive)
        purchase["special_conditions"] = _special_conditions(
            purchase["hard_contract_flags"], decisive
        )
        purchase["special_conditions_short"] = purchase["special_conditions"]
        purchase["special_conditions_evidence"] = decisive
        purchase["extra_costs_estimate"] = None
        purchase["hypothetical_result"] = purchase["current_filter_result"]
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
