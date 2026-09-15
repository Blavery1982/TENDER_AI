"""Правила КАД: только счётчик ответчика, без оценки судебной истории."""
import unittest

from suppliers.arbitration import RESULT_FIELDS, defendant_kad_result, check_kad, minimal_kad_result
from suppliers.verification import HIGH_RISK, MANUAL, PASSED, verify_supplier

INN = "5904993922"


def supplier(kad, domain="shop.example"):
    return {"product_url": f"https://{domain}/x", "verification_checks": {
        "domain_age_years": 8, "requisites_consistent": True, "current_seller_determined": True,
        "company": {"inn": INN, "active": True, "director_changed_within_6_months": False}, "kad_check": kad}}


class ArbitrationTests(unittest.TestCase):
    def test_zero_defendant_cases_do_not_warn(self):
        kad = defendant_kad_result(INN, 0)
        result = verify_supplier(supplier(kad))
        self.assertEqual(kad["kad_status"], "GREEN")
        self.assertEqual(result["verification_status"], PASSED)
        self.assertEqual(result["risk_flags"], [])
        self.assertEqual(set(kad), set(RESULT_FIELDS))

    def test_any_positive_count_is_yellow_including_trusted_domain(self):
        for domain in ("shop.example", "citilink.ru"):
            for count in (1, 3, 300):
                with self.subTest(domain=domain, count=count):
                    kad = defendant_kad_result(INN, count)
                    result = verify_supplier(supplier(kad, domain))
                    self.assertEqual(result["verification_status"], MANUAL)
                    self.assertEqual(kad["kad_status"], "YELLOW")
                    self.assertIn(f"ответчика: {count}", result["verification_comment"])
                    self.assertIn("добросовестность", kad["reason"])

    def test_all_technical_failures_are_manual_with_reason(self):
        for exc in (PermissionError("HTTP 451"), PermissionError("CAPTCHA"),
                    ConnectionError("HTTP 503"), TimeoutError("timeout"), RuntimeError("challenge")):
            with self.subTest(exc=exc):
                def fetch(_):
                    raise exc
                kad = check_kad(INN, fetch)
                self.assertEqual(kad["technical_status"], "KAD_REQUIRES_MANUAL_CHECK")
                self.assertIsNone(kad["defendant_cases_count"])
                self.assertIn(str(exc), kad["reason"])
                self.assertEqual(verify_supplier(supplier(kad))["verification_status"], MANUAL)

    def test_wrong_inn_and_invalid_counts_are_not_zero(self):
        self.assertEqual(check_kad(INN, lambda _: defendant_kad_result("7701097787", 0))
                         ["technical_status"], "KAD_REQUIRES_MANUAL_CHECK")
        for count in (None, -1, True, "0", 1.5):
            with self.subTest(count=count):
                result = defendant_kad_result(INN, count)
                self.assertIsNone(result["defendant_cases_count"])
                self.assertEqual(result["kad_status"], "YELLOW")
        self.assertEqual(check_kad(None)["technical_status"], "KAD_CURRENT_SELLER_UNDETERMINED")

    def test_old_detailed_results_are_rejected_and_new_results_are_whitelisted(self):
        old = {"searched_inn": INN, "checked_in_kad": True, "complete": True,
               "defendant_cases_count": 0, "bankruptcy_cases_count": 1, "case_numbers": ["А40-1/2026"]}
        self.assertEqual(minimal_kad_result(old)["kad_status"], "YELLOW")
        self.assertEqual(check_kad(INN, lambda _: [old])["kad_status"], "YELLOW")
        new = {**defendant_kad_result(INN, 1), "bankruptcy_cases_count": 1,
               "case_numbers": ["А40-1/2026"], "decisions": ["Решение суда"]}
        result = verify_supplier(supplier(new))
        self.assertEqual(result["verification_status"], MANUAL)
        self.assertEqual(set(result["arbitration_cases"]), set(RESULT_FIELDS))
        self.assertEqual(set(result["verification_checks"]["kad_check"]), set(RESULT_FIELDS))
        self.assertNotIn("Решение суда", str(result))

    def test_non_kad_red_risks_are_preserved(self):
        value = supplier(defendant_kad_result(INN, 1))
        value["verification_checks"]["company"]["site_company_mismatch"] = True
        self.assertEqual(verify_supplier(value)["verification_status"], HIGH_RISK)
