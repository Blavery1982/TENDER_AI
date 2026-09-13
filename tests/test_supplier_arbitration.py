import unittest

from suppliers.arbitration import assess_kad_cases, check_kad
from suppliers.verification import HIGH_RISK, MANUAL, verify_supplier


NOW="2026-09-09T00:00:00+00:00"
def case(role="Истец", category="Гражданское", date="2026-01-01", number="А40-1/2026"):
    return {"role":role,"category":category,"date":date,"number":number}
def supplier(kad):
    return {"product_url":"https://shop.ru/x","verification_checks":{
        "domain_age_years":8,"requisites_consistent":True,
        "company":{"inn":"7701097787","active":True,"director_changed_within_6_months":False},
        "kad_check":kad}}


class ArbitrationTests(unittest.TestCase):
    def test_no_cases(self):
        x=assess_kad_cases("1",[],checked_at=NOW); self.assertFalse(x["cases_found"]); self.assertEqual(x["cases_count"],0)
    def test_only_plaintiff(self):
        x=assess_kad_cases("1",[case()],checked_at=NOW); self.assertEqual(x["plaintiff_cases_count"],1); self.assertEqual(x["risk_level"],"informational")
    def test_defendant(self): self.assertEqual(assess_kad_cases("1",[case("Ответчик")],checked_at=NOW)["risk_level"],"attention")
    def test_both_roles(self):
        x=assess_kad_cases("1",[case(),case("Ответчик")],checked_at=NOW); self.assertEqual((x["plaintiff_cases_count"],x["defendant_cases_count"]),(1,1))
    def test_several_recent_defendant(self):
        x=assess_kad_cases("1",[case("Ответчик",number=str(i)) for i in range(3)],checked_at=NOW); self.assertEqual(x["risk_level"],"elevated")
    def test_bankruptcy(self): self.assertEqual(assess_kad_cases("1",[case(category="Банкротное")],checked_at=NOW)["risk_level"],"bankruptcy")
    def test_unavailable(self): self.assertFalse(check_kad("1",lambda _: (_ for _ in ()).throw(ConnectionError()))["checked_in_kad"])
    def test_captcha(self): self.assertIn("CAPTCHA",check_kad("1",lambda _: (_ for _ in ()).throw(PermissionError()))["warnings"][0])
    def test_empty_response_not_no_cases(self): self.assertIsNone(check_kad("1",lambda _:None)["cases_found"])
    def test_ordinary_case_not_fraud(self):
        x=verify_supplier(supplier(assess_kad_cases("1",[case()],checked_at=NOW))); self.assertNotEqual(x["verification_status"],HIGH_RISK)
    def test_bankruptcy_affects_risk(self):
        x=verify_supplier(supplier(assess_kad_cases("1",[case(category="Банкротное")],checked_at=NOW))); self.assertEqual(x["verification_status"],HIGH_RISK)
    def test_user_warning(self): self.assertIn("ЕСТЬ СУДЫ!",assess_kad_cases("1",[case()],checked_at=NOW)["user_summary"])
    def test_unavailable_does_not_break_pipeline(self):
        x=verify_supplier(supplier(check_kad("1"))); self.assertIn(x["verification_status"],{MANUAL,"✅ ПОСТАВЩИК ПРОШЁЛ ПРОВЕРКУ"})


if __name__ == "__main__": unittest.main()
