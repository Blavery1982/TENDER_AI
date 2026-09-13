from __future__ import annotations

import unittest
from xml.etree import ElementTree as ET

from eat.integration_client import (
    EAT_NAMESPACE,
    OBJECT_NAMESPACE,
    EatIntegrationClient,
    EatIntegrationConfig,
    build_request_order_list,
    build_request_order_notification,
    normalize_order_notification,
    parse_order_references,
)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class FakeOpener:
    def __init__(self, responses: list[bytes]):
        self.responses = iter(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return FakeResponse(next(self.responses))


def xml(tag: str, body: str) -> bytes:
    return (
        f'<{tag} xmlns="{OBJECT_NAMESPACE}" xmlns:eat="{EAT_NAMESPACE}" '
        f'eat:Version="2.37" eat:RequestUID="uid">{body}</{tag}>'
    ).encode()


class EatIntegrationClientTests(unittest.TestCase):
    def test_request_xml_has_required_fields_and_unqualified_ext_system(self):
        root = ET.fromstring(build_request_order_list(
            version="2.37", request_uid="uid", ext_system="12345"
        ))
        self.assertEqual(root.tag.rsplit("}", 1)[-1], "requestOrderList")
        self.assertEqual(root.attrib[f"{{{EAT_NAMESPACE}}}Version"], "2.37")
        self.assertEqual(root.attrib[f"{{{EAT_NAMESPACE}}}RequestUID"], "uid")
        self.assertEqual(root[0].tag, f"{{{OBJECT_NAMESPACE}}}extSystem")
        self.assertEqual(root[0].text, "12345")

    def test_order_notification_xml_contains_number(self):
        root = ET.fromstring(build_request_order_notification(
            version="2.37", request_uid="uid", ext_system="12345", order_number="A-1"
        ))
        self.assertEqual([child.text for child in root], ["A-1", "12345"])

    def test_parse_list_references(self):
        root = ET.fromstring(xml(
            "responseOrderList",
            "<orders><regNumber>A-1</regNumber></orders>"
            "<orders><OrderNumber>A-2</OrderNumber></orders>",
        ))
        self.assertEqual([item["number"] for item in parse_order_references(root)], ["A-1", "A-2"])

    def test_normalization_preserves_filter_fields(self):
        root = ET.fromstring(xml(
            "responseOrderNotification",
            "<OrderNumber>A-1</OrderNumber>"
            "<Subject>Поставка ИБП</Subject>"
            "<typePurchase>44-ФЗ</typePurchase>"
            "<maxOrderCost>150 000,50</maxOrderCost>"
            "<orderExpireDate>2027-01-01T10:00:00Z</orderExpireDate>"
            "<Customer><name>Заказчик</name></Customer>"
            "<DeliveryAddress><regionName>г. Москва</regionName>"
            "<street>Тверская, 1</street></DeliveryAddress>"
            "<Product><name>ИБП</name><requirements>220 В</requirements>"
            "<availableVolume>2</availableVolume></Product>",
        ))
        normalized = normalize_order_notification(root)
        self.assertEqual(normalized["id"], "A-1")
        self.assertEqual(normalized["purchaseTypeTitle"], "44-ФЗ")
        self.assertEqual(normalized["price"], 150000.5)
        self.assertEqual(normalized["organizerInfo"]["name"], "Заказчик")
        self.assertEqual(normalized["deliveryInfos"][0]["deliveryAddress"]["regionName"], "г. Москва")
        self.assertEqual(normalized["lotItems"][0]["name"], "ИБП")
        self.assertEqual(normalized["lotItems"][0]["quantity"], "2")

    def test_client_polls_processing_result_without_logging_secret(self):
        opener = FakeOpener([
            xml("responseProcessingResult", ""),
            xml("responseProcessingResult", "<responseOrderList>"
                "<orders><regNumber>A-1</regNumber></orders>"
                "</responseOrderList>"),
        ])
        config = EatIntegrationConfig(
            token="SECRET", ext_system="12345", poll_interval_seconds=0, poll_attempts=2
        )
        result = EatIntegrationClient(config, opener=opener).get_active_order_references()
        self.assertEqual(result[0]["number"], "A-1")
        self.assertEqual(len(opener.requests), 2)
        request = opener.requests[0][0]
        self.assertEqual(request.get_header("Authorization"), "Bearer SECRET")
        self.assertNotIn("SECRET", request.data.decode())


if __name__ == "__main__":
    unittest.main()
