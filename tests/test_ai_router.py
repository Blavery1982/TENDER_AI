import json
import tempfile
import unittest
from pathlib import Path

from ai.base import (AI_ASSISTANCE_UNAVAILABLE, AMBIGUOUS, AVAILABLE, ERROR,
                     CONFIGURED, LOW_CONFIDENCE, MANUAL_REVIEW_REQUIRED, RESOLVED,
                     UNAVAILABLE, UNKNOWN, CoreDecision, ProviderHealth,
                     ProviderResponse)
from ai.config import load_ai_config
from ai.openai_provider import OpenAIProvider
from ai.router import AIRouter
from ai.yandex_provider import YandexAIProvider
from core_knowledge.registry import build_rule_candidate, load_approved_rules


def config(mode="AUTO", **changes):
    return {"ai_mode": mode, "fallback_to_core": True,
            "explicit_mode_fallback_to_other_ai": False,
            "request_timeout_seconds": 1, "ai_daily_limit": None,
            "ai_monthly_limit": None, **changes}


class FakeProvider:
    def __init__(self, name, health=AVAILABLE, response=None, error=None):
        self.name=name; self.health=health; self.response=response; self.error=error
        self.health_calls=0; self.calls=0

    def health_check(self):
        self.health_calls += 1
        return ProviderHealth(self.name, self.health, self.health == AVAILABLE,
                              self.health == AVAILABLE, self.health)

    def analyze(self, request, *, timeout_seconds):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response or ProviderResponse(self.name, RESOLVED, value="ai")


class AIRouterTests(unittest.TestCase):
    def test_default_global_mode_is_auto(self):
        self.assertEqual(load_ai_config()["ai_mode"], "AUTO")

    def test_off_runs_without_ai(self):
        provider=FakeProvider("OPENAI")
        result=AIRouter(config=config("OFF"),providers={"OPENAI":provider}).resolve(
            "task", {}, reason="unknown", core_solver=lambda _:CoreDecision(UNKNOWN))
        self.assertEqual(result.status, AI_ASSISTANCE_UNAVAILABLE)
        self.assertEqual((provider.health_calls,provider.calls),(0,0))

    def test_auto_core_confident_never_checks_ai(self):
        openai=FakeProvider("OPENAI"); yandex=FakeProvider("YANDEX")
        result=AIRouter(config=config(),providers={"OPENAI":openai,"YANDEX":yandex}).resolve(
            "task", {"x":1}, reason="not needed",
            core_solver=lambda _:CoreDecision(RESOLVED,"core",.99,"known rule"))
        self.assertEqual((result.value,result.source),("core","CORE"))
        self.assertEqual((openai.health_calls,yandex.health_calls),(0,0))

    def test_auto_uses_openai_for_uncertain_core(self):
        openai=FakeProvider("OPENAI",response=ProviderResponse("OPENAI",RESOLVED,"openai"))
        yandex=FakeProvider("YANDEX")
        result=AIRouter(config=config(),providers={"OPENAI":openai,"YANDEX":yandex}).resolve(
            "task", {}, reason="ambiguous", core_solver=lambda _:CoreDecision(AMBIGUOUS))
        self.assertEqual((result.value,result.source),("openai","OPENAI"))
        self.assertEqual(yandex.health_calls,0)

    def test_auto_falls_back_to_yandex(self):
        openai=FakeProvider("OPENAI",health=UNAVAILABLE)
        yandex=FakeProvider("YANDEX",response=ProviderResponse("YANDEX",RESOLVED,"yandex"))
        result=AIRouter(config=config(),providers={"OPENAI":openai,"YANDEX":yandex}).resolve(
            "task", {}, reason="low confidence", core_solver=lambda _:CoreDecision(LOW_CONFIDENCE))
        self.assertEqual((result.value,result.source),("yandex","YANDEX"))
        self.assertEqual(yandex.calls,1)

    def test_auto_without_providers_returns_manual_review(self):
        providers={"OPENAI":FakeProvider("OPENAI",health=UNAVAILABLE),
                   "YANDEX":FakeProvider("YANDEX",health=UNAVAILABLE)}
        result=AIRouter(config=config(),providers=providers).resolve(
            "task", {}, reason="unknown", core_solver=lambda _:CoreDecision(UNKNOWN))
        self.assertEqual(result.status, MANUAL_REVIEW_REQUIRED)

    def test_explicit_openai_unavailable_safe_fallback(self):
        openai=FakeProvider("OPENAI",health=UNAVAILABLE)
        yandex=FakeProvider("YANDEX")
        result=AIRouter(config=config("OPENAI"),providers={"OPENAI":openai,"YANDEX":yandex}).resolve(
            "task", {}, reason="unknown", core_solver=lambda _:CoreDecision(UNKNOWN))
        self.assertEqual(result.status, MANUAL_REVIEW_REQUIRED)
        self.assertEqual(yandex.health_calls,0)

    def test_explicit_yandex_unavailable_safe_fallback(self):
        yandex=FakeProvider("YANDEX",health=UNAVAILABLE)
        result=AIRouter(config=config("YANDEX"),providers={"YANDEX":yandex}).resolve(
            "task", {}, reason="unknown", core_solver=lambda _:CoreDecision(UNKNOWN))
        self.assertEqual(result.status, MANUAL_REVIEW_REQUIRED)

    def test_missing_credentials_does_not_affect_core(self):
        openai=OpenAIProvider(credential_probe=lambda:False)
        yandex=YandexAIProvider(credential_probe=lambda:False)
        result=AIRouter(config=config(),providers={"OPENAI":openai,"YANDEX":yandex}).resolve(
            "task", {}, reason="known", core_solver=lambda _:CoreDecision(RESOLVED,42))
        self.assertEqual((result.value,result.source),(42,"CORE"))

    def test_health_distinguishes_configured_from_available(self):
        configured=OpenAIProvider(credential_probe=lambda:True)
        available=OpenAIProvider(credential_probe=lambda:True,
                                 availability_probe=lambda:True,
                                 client=lambda request, timeout:ProviderResponse(
                                     "OPENAI",RESOLVED,"ok"))
        self.assertEqual(configured.health_check().status,CONFIGURED)
        self.assertEqual(available.health_check().status,AVAILABLE)

    def test_timeout_falls_back_once_and_pipeline_continues(self):
        openai=FakeProvider("OPENAI",error=TimeoutError())
        yandex=FakeProvider("YANDEX",response=ProviderResponse("YANDEX",RESOLVED,"safe"))
        result=AIRouter(config=config(),providers={"OPENAI":openai,"YANDEX":yandex}).resolve(
            "task", {}, reason="timeout test", core_solver=lambda _:CoreDecision(UNKNOWN))
        self.assertEqual((result.value,result.source),("safe","YANDEX"))
        self.assertEqual((openai.calls,yandex.calls),(1,1))

    def test_provider_error_isolated(self):
        providers={"OPENAI":FakeProvider("OPENAI",error=RuntimeError("secret value")),
                   "YANDEX":FakeProvider("YANDEX",health=UNAVAILABLE)}
        result=AIRouter(config=config(),providers=providers).resolve(
            "task", {}, reason="error test", core_solver=lambda _:CoreDecision(UNKNOWN))
        self.assertEqual(result.status, MANUAL_REVIEW_REQUIRED)
        self.assertNotIn("secret value",str(result.provider_attempts))

    def test_ai_rule_candidate_does_not_write_core(self):
        before=load_approved_rules()
        candidate=build_rule_candidate(skill="reader",description="test")
        openai=FakeProvider("OPENAI",response=ProviderResponse(
            "OPENAI",RESOLVED,"answer",core_rule_candidate=candidate))
        result=AIRouter(config=config(),providers={"OPENAI":openai}).resolve(
            "task", {}, reason="new case", core_solver=lambda _:CoreDecision(UNKNOWN))
        self.assertEqual(result.core_rule_candidate["status"],"CORE_RULE_CANDIDATE")
        self.assertFalse(result.core_rule_candidate["approved"])
        self.assertEqual(load_approved_rules(),before)

    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"ai.json"
            path.write_text(json.dumps({"ai_mode":"WRONG"}),encoding="utf-8")
            with self.assertRaises(ValueError):
                load_ai_config(path)


if __name__ == "__main__":
    unittest.main()
