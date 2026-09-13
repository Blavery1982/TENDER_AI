import copy
import unittest

from pipeline.orchestrator import AUDIT_MODEL, CARD, SUPPLIERS, VERIFICATION, build_pipeline, load_json
from security.customer_check import (STATUS_CASES, STATUS_MANUAL, STATUS_SERIOUS,
                                     build_customer_check, extract_customer)
from suppliers.arbitration import assess_kad_cases
from suppliers.verification import verify_supplier

NOW="2026-09-09T00:00:00+00:00"
def case(role="Истец",category="Гражданское",date="2026-01-01",number="А40-1/2026"):
    return {"role":role,"category":category,"date":date,"number":number}
def fetch(cases): return lambda _inn: cases


class CustomerCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.card=load_json(CARD); cls.audit=load_json(AUDIT_MODEL)
        cls.supplier=load_json(SUPPLIERS); cls.deep=load_json(VERIFICATION)

    def test_01_inn_from_card(self):
        x=extract_customer({"raw":{"customer":{"inn":"7701097787","name":"ООО Тест"}}},[])
        self.assertEqual(x["customer_inn"],"7701097787"); self.assertIn("ЕАТ",x["customer_data_source"])
    def test_02_inn_from_document(self):
        x=extract_customer({},[{"text":"Заказчик ФКУ ТЕСТ ИНН: 7718115635, КПП: 771801001"}])
        self.assertEqual(x["customer_inn"],"7718115635"); self.assertEqual(x["customer_kpp"],"771801001")
    def test_03_inn_absent(self): self.assertIsNone(extract_customer({},[])["customer_inn"])
    def test_04_shared_arbitration_shape(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([]))
        self.assertIn("arbitration_evidence",x); self.assertTrue(x["arbitration_evidence"]["checked_in_kad"])
    def test_05_only_plaintiff(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([case()]))
        self.assertEqual(x["arbitration_status"],STATUS_CASES); self.assertNotIn("неплат",x["payment_risk_status"].lower())
    def test_06_defendant(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([case("Ответчик")]))
        self.assertIn("риск оплаты",x["payment_risk_status"])
    def test_07_several_recent_defendant(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([case("Ответчик",number=str(i)) for i in range(3)]))
        self.assertEqual(x["recent_cases_count"],3)
    def test_08_mixed_roles(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([case(),case("Ответчик")]))
        self.assertEqual((x["plaintiff_cases_count"],x["defendant_cases_count"]),(1,1))
    def test_09_bankruptcy(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([case(category="Банкротное")]))
        self.assertEqual(x["arbitration_status"],STATUS_SERIOUS)
    def test_10_kad_unavailable(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],lambda _: (_ for _ in ()).throw(ConnectionError()))
        self.assertEqual(x["arbitration_status"],STATUS_MANUAL)
    def test_11_captcha_not_bypassed(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],lambda _: (_ for _ in ()).throw(PermissionError()))
        self.assertIn("CAPTCHA",str(x["warnings"]))
    def test_12_unavailable_not_no_cases(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[]); self.assertIsNone(x["cases_count"])
    def test_13_ordinary_case_not_rejection(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([case("Ответчик")]))
        self.assertNotIn("НЕ УЧАСТВОВАТЬ",x["payment_risk_status"])
    def test_14_cases_label(self):
        x=build_customer_check({"raw":{"customer":{"inn":"7701097787"}}},[],fetch([case()])); self.assertIn("ЕСТЬ СУДЫ!",x["arbitration_status"])
    def test_15_customer_check_in_pipeline(self): self.assertIn("customer_check",build_pipeline(copy.deepcopy(self.card),copy.deepcopy(self.audit),copy.deepcopy(self.supplier),copy.deepcopy(self.deep)))
    def test_16_pipeline_continues_without_kad(self):
        x=build_pipeline(copy.deepcopy(self.card),copy.deepcopy(self.audit),copy.deepcopy(self.supplier),copy.deepcopy(self.deep)); self.assertIn("technical_audit",x)
    def test_17_manual_action_has_inn(self):
        x=build_customer_check({},[{"text":"ИНН 7718115635"}]); self.assertEqual(x["manual_actions_required"][0]["customer_inn"],"7718115635")
    def test_18_supplier_arbitration_still_works(self):
        kad=assess_kad_cases("1",[case()],checked_at=NOW)
        x=verify_supplier({"product_url":"https://shop.ru","verification_checks":{"kad_check":kad}})
        self.assertIn("arbitration_cases",x)
    def test_19_full_card_customer_fields_have_priority(self):
        card={"raw":{"customer":{
            "name":'ФКУ "ДИРЕКЦИЯ"',"inn":"7703255580","kpp":"770301001",
            "ogrn":"1027700278803","address":"Москва, ул. Тестовая, 1",
            "contactFio":"Иванов И.И.","contactEmail":"mail@example.ru",
            "contactPhone":"+7(499)000-00-00"}}}
        x=extract_customer(card,[{"text":"Заказчик ООО Ошибка ИНН 7718115635 КПП 771801001"}])
        self.assertEqual(x["customer_name"],'ФКУ "ДИРЕКЦИЯ"')
        self.assertEqual(x["customer_inn"],"7703255580")
        self.assertEqual(x["customer_kpp"],"770301001")
        self.assertEqual(x["customer_address"],"Москва, ул. Тестовая, 1")
        self.assertEqual(x["customer_email"],"mail@example.ru")
        self.assertEqual(x["customer_phone"],"+7(499)000-00-00")
        self.assertIn("Структурированные",x["customer_data_source"])
    def test_20_customer_inn_is_passed_to_kad(self):
        received=[]
        def kad_fetcher(inn):
            received.append(inn)
            return []
        x=build_customer_check({"raw":{"customer":{"inn":"7703255580"}}},[],kad_fetcher)
        self.assertEqual(received,["7703255580"])
        self.assertEqual(x["arbitration_evidence"]["searched_inn"],"7703255580")


if __name__=="__main__": unittest.main()
