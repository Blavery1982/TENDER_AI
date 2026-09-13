"""Запись отдельного результата сквозного теста."""
from __future__ import annotations
import json
from pathlib import Path
from pipeline.orchestrator import run_pipeline

OUTPUT=Path(__file__).resolve().parent.parent/"data/end_to_end_third_test.json"

def run_test():
    result=run_pipeline()
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result

if __name__=="__main__": print(json.dumps(run_test(),ensure_ascii=False,indent=2))
