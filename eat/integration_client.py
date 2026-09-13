"""Официальный XML-интеграционный клиент ЕАТ.

Клиент намеренно отделён от браузерного сборщика. Он не подаёт ценовые
предложения и не создаёт корзины: используются только методы чтения списка
закупочных сессий и их подробностей.
"""

from __future__ import annotations

import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"
API_BASE_URL = "https://agregatoreat.ru/integration/ecom/rest/api"
ORDER_LIST_URL = f"{API_BASE_URL}/order/requestOrderList"
ORDER_NOTIFICATION_URL = f"{API_BASE_URL}/order/orderNotification"
PROCESSING_RESULT_URL = f"{API_BASE_URL}/processingResult"

EAT_NAMESPACE = "http://agregatoreat.ru/eat/"
OBJECT_NAMESPACE = "http://agregatoreat.ru/eat/object-types/"


class EatIntegrationError(RuntimeError):
    """Безопасная ошибка API без вывода токена или тела ответа."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class EatIntegrationConfigError(EatIntegrationError):
    """Конфигурация API неполная или некорректная."""


@dataclass(frozen=True)
class EatIntegrationConfig:
    token: str
    ext_system: str
    version: str = "2.37"
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    timeout_seconds: float = 30.0
    poll_interval_seconds: float = 2.0
    poll_attempts: int = 15

    def auth_value(self) -> str:
        return f"{self.auth_scheme} {self.token}".strip()


def _dotenv_values(path: Path = DOTENV_PATH) -> dict[str, str]:
    """Прочитать простые KEY=VALUE строки без вывода секретов."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _setting(name: str, dotenv: dict[str, str], default: str = "") -> str:
    return os.environ.get(name, dotenv.get(name, default)).strip()


def load_integration_config(*, dotenv_path: Path = DOTENV_PATH) -> EatIntegrationConfig:
    dotenv = _dotenv_values(dotenv_path)
    token = _setting("EAT_API_TOKEN", dotenv)
    ext_system = _setting("EAT_EXT_SYSTEM_NUMBER", dotenv)
    if not token:
        raise EatIntegrationConfigError(
            "Не задан EAT_API_TOKEN в окружении или локальном .env"
        )
    if not ext_system:
        raise EatIntegrationConfigError(
            "Не задан EAT_EXT_SYSTEM_NUMBER: его номер нужен для внешней ИС ЕАТ"
        )
    try:
        timeout = float(_setting("EAT_API_TIMEOUT_SECONDS", dotenv, "30"))
        poll_interval = float(_setting("EAT_API_POLL_INTERVAL_SECONDS", dotenv, "2"))
        poll_attempts = int(_setting("EAT_API_POLL_ATTEMPTS", dotenv, "15"))
    except ValueError as exc:
        raise EatIntegrationConfigError("Числовые параметры ЕАТ заданы неверно") from exc
    if timeout <= 0 or poll_interval < 0 or poll_attempts <= 0:
        raise EatIntegrationConfigError("Числовые параметры ЕАТ должны быть положительными")
    return EatIntegrationConfig(
        token=token,
        ext_system=ext_system,
        version=_setting("EAT_API_VERSION", dotenv, "2.37"),
        auth_header=_setting("EAT_API_AUTH_HEADER", dotenv, "Authorization"),
        auth_scheme=_setting("EAT_API_AUTH_SCHEME", dotenv, "Bearer"),
        timeout_seconds=timeout,
        poll_interval_seconds=poll_interval,
        poll_attempts=poll_attempts,
    )


def _request_root(name: str, *, version: str, request_uid: str, ext_system: str) -> ET.Element:
    root = ET.Element(
        f"{{{OBJECT_NAMESPACE}}}{name}",
        {f"{{{EAT_NAMESPACE}}}Version": version,
         f"{{{EAT_NAMESPACE}}}RequestUID": request_uid},
    )
    return root


def _serialize(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_request_order_list(*, version: str, request_uid: str, ext_system: str) -> bytes:
    """Сформировать XML запроса списка активных закупочных сессий."""
    root = _request_root(
        "requestOrderList", version=version, request_uid=request_uid,
        ext_system=ext_system,
    )
    ET.SubElement(root, f"{{{OBJECT_NAMESPACE}}}extSystem").text = ext_system
    return _serialize(root)


def build_request_processing_result(
    *, version: str, request_uid: str, ext_system: str
) -> bytes:
    root = _request_root(
        "requestProcessingResult", version=version, request_uid=request_uid,
        ext_system=ext_system,
    )
    ET.SubElement(root, f"{{{OBJECT_NAMESPACE}}}extSystem").text = ext_system
    return _serialize(root)


def build_request_order_notification(
    *, version: str, request_uid: str, ext_system: str, order_number: str
) -> bytes:
    root = _request_root(
        "requestOrderNotification", version=version, request_uid=request_uid,
        ext_system=ext_system,
    )
    ET.SubElement(root, f"{{{OBJECT_NAMESPACE}}}OrderNumber").text = order_number
    ET.SubElement(root, f"{{{OBJECT_NAMESPACE}}}extSystem").text = ext_system
    return _serialize(root)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join(part.strip() for part in element.itertext() if part.strip())
    return value or None


def _first_element(root: ET.Element, names: Iterable[str]) -> ET.Element | None:
    wanted = {name.casefold() for name in names}
    for element in root.iter():
        if _local_name(element.tag).casefold() in wanted:
            return element
    return None


def _first_text(root: ET.Element, names: Iterable[str]) -> str | None:
    return _text(_first_element(root, names))


def _element_record(element: ET.Element) -> dict[str, Any]:
    """Плоская запись прямых XML-полей, повторяющиеся поля становятся списком."""
    values: dict[str, Any] = {}
    for child in list(element):
        key = _local_name(child.tag)
        value: Any = _text(child)
        if list(child):
            value = _element_record(child)
        if key in values:
            values[key] = values[key] if isinstance(values[key], list) else [values[key]]
            values[key].append(value)
        else:
            values[key] = value
    return values


def _parse_xml(body: bytes) -> ET.Element:
    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise EatIntegrationError("ЕАТ вернул некорректный XML") from exc


def _parse_number(value: str | None) -> int | float | None:
    if not value:
        return None
    normalized = re.sub(r"[^0-9,.-]", "", value.replace(" ", ""))
    if not normalized:
        return None
    try:
        number = float(normalized.replace(",", "."))
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def parse_order_references(root: ET.Element) -> list[dict[str, Any]]:
    """Извлечь ссылки из responseOrderList."""
    container = _first_element(root, {"responseOrderList"})
    if container is None:
        return []
    references: list[dict[str, Any]] = []
    for child in list(container):
        if _local_name(child.tag).casefold() != "orders":
            continue
        record = _element_record(child)
        number = record.get("regNumber") or record.get("OrderNumber")
        if number:
            references.append({"number": str(number), "raw": record})
    return references


def normalize_order_notification(root: ET.Element) -> dict[str, Any]:
    """Привести XML карточки к полям, которые ожидает filter_purchase_v2."""
    notification = _first_element(root, {"responseOrderNotification"}) or root
    product_elements = [
        element for element in notification.iter()
        if _local_name(element.tag).casefold() == "product"
    ]
    lot_items: list[dict[str, Any]] = []
    for product in product_elements:
        fields = _element_record(product)
        name = (
            fields.get("name") or fields.get("Name") or fields.get("productName")
            or fields.get("ProductName") or fields.get("description")
            or fields.get("Description")
        )
        description = fields.get("requirements") or fields.get("Requirements")
        item: dict[str, Any] = {"name": name or ""}
        if description:
            item["description"] = description
        for source, target in (("availableVolume", "quantity"), ("OKEI", "unit"),
                               ("EATClassifierRefCode", "eatCode"),
                               ("OKPD", "okpd2")):
            if fields.get(source) is not None:
                item[target] = fields[source]
        lot_items.append(item)

    customer = _first_element(notification, {"Customer", "customer"})
    customer_fields = _element_record(customer) if customer is not None else {}
    customer_name = (
        customer_fields.get("name") or customer_fields.get("Name")
        or customer_fields.get("fullName") or customer_fields.get("FullName")
    )
    delivery = _first_element(notification, {"DeliveryAddress", "deliveryAddress", "deliveryPlace"})
    delivery_fields = _element_record(delivery) if delivery is not None else {}
    delivery_text = _text(delivery)
    region = (
        delivery_fields.get("regionName") or delivery_fields.get("RegionName")
        or delivery_fields.get("region") or delivery_fields.get("Region")
    )
    trade_number = _first_text(notification, {"OrderNumber", "orderNumber", "regNumber"})
    subject = _first_text(notification, {"Subject", "subject", "orderName", "OrderName"})
    if not subject and lot_items:
        subject = str(lot_items[0].get("name") or "")
    purchase_type = _first_text(notification, {"typePurchase", "purchaseTypeTitle", "PurchaseTypeTitle"})
    price_text = _first_text(notification, {"maxOrderCost", "MaxOrderCost", "price", "Price"})
    start_date = _first_text(notification, {"startDate", "startTime", "publishDate", "PublishDate"})
    end_date = _first_text(notification, {"orderExpireDate", "expireTime", "applicationFillingEndDate"})

    address: dict[str, Any] = {}
    if region:
        address["regionName"] = region
    if delivery_text:
        address["formattedFullInfo"] = delivery_text

    return {
        "id": trade_number,
        "tradeNumber": trade_number,
        "subject": subject or "",
        "price": _parse_number(price_text),
        "purchaseTypeTitle": purchase_type,
        "organizerInfo": {"name": customer_name or "", **customer_fields},
        "deliveryInfos": [{"deliveryAddress": address}] if address else [],
        "publishDate": start_date,
        "applicationFillingEndDate": end_date,
        "lotItems": lot_items,
        "isTradeInfoHiddenByPrivacyAgreement": False,
        "hideDetailsForUnauthorized": False,
        "stateDefenseOrder": False,
        "source": "eat_official_integration_api",
    }


def _safe_error(status: int, body: bytes) -> str:
    text = body.decode("utf-8", "ignore").casefold()
    if status in {401, 403}:
        return "ЕАТ отклонил авторизацию токена (проверьте токен, заголовок и права ИС)"
    if "xml" in text or status in {400, 422}:
        return "ЕАТ отклонил XML-запрос; проверьте версию, extSystem и схему Альбома ТФФ"
    return f"Ошибка интеграционного API ЕАТ: HTTP {status}"


class EatIntegrationClient:
    """Только чтение: список активных закупок и подробности карточек."""

    def __init__(self, config: EatIntegrationConfig, *, opener: Callable[..., Any] = urlopen):
        self.config = config
        self.opener = opener

    def _post(self, url: str, body: bytes) -> ET.Element:
        request = Request(
            url,
            data=body,
            headers={
                self.config.auth_header: self.config.auth_value(),
                "Accept": "application/xml",
                "Content-Type": "application/xml; charset=utf-8",
                "User-Agent": "TENDER_AI/1.0 official-eat-integration",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.config.timeout_seconds) as response:
                payload = response.read()
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            payload = exc.read(4096)
            raise EatIntegrationError(_safe_error(exc.code, payload), status=exc.code) from None
        except URLError as exc:
            raise EatIntegrationError("Не удалось подключиться к endpoint ЕАТ") from exc
        if not 200 <= status < 300:
            raise EatIntegrationError(_safe_error(status, payload), status=status)
        return _parse_xml(payload)

    def request_order_list(self) -> tuple[str, ET.Element]:
        request_uid = str(uuid.uuid4())
        root = self._post(
            ORDER_LIST_URL,
            build_request_order_list(
                version=self.config.version,
                request_uid=request_uid,
                ext_system=self.config.ext_system,
            ),
        )
        return request_uid, root

    def request_processing_result(self, request_uid: str) -> ET.Element:
        return self._post(
            PROCESSING_RESULT_URL,
            build_request_processing_result(
                version=self.config.version,
                request_uid=request_uid,
                ext_system=self.config.ext_system,
            ),
        )

    def _wait_for(
        self, request_uid: str, initial: ET.Element, result_name: str
    ) -> ET.Element:
        if _first_element(initial, {result_name}) is not None:
            return initial
        for attempt in range(self.config.poll_attempts):
            if attempt:
                time.sleep(self.config.poll_interval_seconds)
            current = self.request_processing_result(request_uid)
            if _first_element(current, {result_name}) is not None:
                return current
            if _first_element(current, {"ProcessingError", "responseViolation"}) is not None:
                raise EatIntegrationError("ЕАТ вернул ошибку обработки запроса")
        raise EatIntegrationError(
            "ЕАТ не вернул результат за отведённое время; обработка не завершена"
        )

    def get_active_order_references(self) -> list[dict[str, Any]]:
        request_uid, initial = self.request_order_list()
        result = self._wait_for(request_uid, initial, "responseOrderList")
        return parse_order_references(result)

    def request_order_notification(self, order_number: str) -> tuple[str, ET.Element]:
        request_uid = str(uuid.uuid4())
        root = self._post(
            ORDER_NOTIFICATION_URL,
            build_request_order_notification(
                version=self.config.version,
                request_uid=request_uid,
                ext_system=self.config.ext_system,
                order_number=order_number,
            ),
        )
        return request_uid, root

    def get_order(self, order_number: str) -> dict[str, Any]:
        request_uid, initial = self.request_order_notification(order_number)
        result = self._wait_for(request_uid, initial, "responseOrderNotification")
        return normalize_order_notification(result)

    def get_active_orders(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        references = self.get_active_order_references()
        if limit is not None:
            references = references[:limit]
        return [self.get_order(str(reference["number"])) for reference in references]

