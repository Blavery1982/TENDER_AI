"""Контроль только сохранённой закупки 100205573126100053, без сети."""
from __future__ import annotations
import json
from pathlib import Path
from pipeline.orchestrator import run_pipeline

ROOT=Path(__file__).resolve().parent.parent
OUTPUT=ROOT/"data/customer_check_third_test.json"

def run_test():
    result=run_pipeline()
    compact={"tradeNumber":result["procurement"]["procurement_number"],
             "customer_check":result["customer_check"],
             "customer_check_google_sheets":result["customer_check_google_sheets"],
             "pipeline_continued_to_procurement_audit":bool(result.get("technical_audit")),
             "stage_log":result["stage_log"]}
    OUTPUT.write_text(json.dumps(compact,ensure_ascii=False,indent=2),encoding="utf-8")
    return compact

if __name__=="__main__": print(json.dumps(run_test(),ensure_ascii=False,indent=2))
