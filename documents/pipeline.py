"""Общий production-like интерфейс локальных документов и procurement audit."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from documents.item_sources import resolve_item_sources
from documents.procurement_audit import (classify_document,
                                         extract_document_with_evidence)
from eat.contract_analysis import (_special_conditions, analyze_access_conditions,
                                   analyze_text)
from filters.eat_filters import load_config

ROOT=Path(__file__).resolve().parent.parent
CACHE_DIR=ROOT/"data/cache/document_extraction"
CACHE_VERSION="text-extraction-v1_tesseract5_rus-eng"


def document_sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()


def _cache_path(digest: str, cache_dir: Path) -> Path:
    return cache_dir/f"{digest}_{CACHE_VERSION}.json"


def process_procurement_documents(procurement: dict, document_source: list[dict] | None=None,
                                  *, cache_dir: Path=CACHE_DIR,
                                  extractor: Callable[[Path],dict]=extract_document_with_evidence,
                                  logger=None) -> dict:
    """Dry-run читает только local_path; будущий live source сможет дать тот же список."""
    pid=str(procurement.get("purchase_id") or (procurement.get("raw") or {}).get("id") or "unknown")
    results=[]; warnings=[]; processed=failed=ocr_count=mixed_count=cache_hits=0
    source=document_source if document_source is not None else procurement.get("documents") or []
    cache_dir.mkdir(parents=True,exist_ok=True)
    for number,info in enumerate(source,1):
        path=Path(info.get("local_path") or "")
        base={"document_number":number,"document_name":info.get("file_name") or path.name,
              "source_document_type":info.get("document_type"), "source_section":info.get("source_section"),
              "item_number":info.get("item_number"), "position_number":info.get("position_number"),
              "document_type":info.get("document_type"),"source":info.get("download_url"),
              "local_path":str(path) if str(path) else None}
        if not path.is_file():
            warning="Документ отсутствует в локальном dry-run"; warnings.append(warning)
            results.append({**base,"status":"missing","extraction_method":None,"pages":[],
                            "text_available":False,"ocr_used":False,"ocr_confidence":None,
                            "warnings":[warning],"error":None,"text":""}); continue
        digest=document_sha256(path); cache_path=_cache_path(digest,cache_dir)
        try:
            if cache_path.exists():
                extracted=json.loads(cache_path.read_text(encoding="utf-8")); cache_hit=True; cache_hits+=1
            else:
                extracted=extractor(path); cache_hit=False
                cache_path.write_text(json.dumps(extracted,ensure_ascii=False,indent=2),encoding="utf-8")
            text=extracted.get("text") or ""; pages=extracted.get("pages") or []
            page_failed=any(x.get("read_method")=="failed" for x in pages)
            status="partial" if page_failed else extracted.get("document_parse_status","analyzed")
            if status in {"analyzed","partial"}: processed+=1
            else: failed+=1
            method=extracted.get("read_method"); ocr=bool(extracted.get("ocr_pages"))
            ocr_count+=ocr; mixed_count+=method=="mixed"
            kinds=classify_document(base["document_name"],text)
            result={**base,"document_type":kinds,"sha256":digest,"cache_key":cache_path.name,
                    "cache_reference":str(cache_path),"cache_hit":cache_hit,"status":status,
                    "extraction_method":method,"pages":pages,"text_available":bool(text.strip()),
                    "ocr_used":ocr,"ocr_confidence":_average_confidence(pages),
                    "warnings":extracted.get("warnings") or [],"error":None,"text":text}
            warnings.extend(result["warnings"]); results.append(result)
            if logger: logger.info("procurement=%s document=%s stage=TEXT_EXTRACTION status=%s method=%s cache=%s",pid,number,status,method,cache_hit)
        except Exception as exc:
            failed+=1; error=f"{type(exc).__name__}: document extraction failed"
            warnings.append(f"{base['document_name']}: {error}")
            results.append({**base,"status":"failed","extraction_method":None,"pages":[],
                            "text_available":False,"ocr_used":False,"ocr_confidence":None,
                            "warnings":[],"error":error,"text":""})
            if logger: logger.error("procurement=%s document=%s stage=TEXT_EXTRACTION status=failed error=%s",pid,number,error)
    return {"procurement_id":pid,"documents_found":len(source),"documents_processed":processed,
            "documents_failed":failed,"document_results":results,
            "combined_text":"\n\n".join(x["text"] for x in results if x.get("text")),
            "warnings":list(dict.fromkeys(warnings)),
            "extraction_summary":{"text_layer_documents":sum(x.get("extraction_method")=="text_layer" for x in results),
                                  "ocr_documents":ocr_count,"mixed_documents":mixed_count,
                                  "partial_documents":sum(x.get("status")=="partial" for x in results),
                                  "cache_hits":cache_hits}}


def _average_confidence(pages: list[dict]) -> float | None:
    values=[x.get("average_confidence") for x in pages if x.get("average_confidence") is not None]
    return round(sum(values)/len(values),2) if values else None


def audit_from_extraction(procurement: dict, extraction: dict) -> dict:
    raw=procurement.get("raw") or procurement; lot=raw.get("lot") or raw
    docs=extraction["document_results"]; combined=extraction["combined_text"]
    config=load_config(); hard=[]; extras=[]
    for document in docs:
        if not document.get("text"): continue
        flags,found=analyze_text(document["text"],document["document_name"],config)
        found.extend(analyze_access_conditions(document["text"],document["document_name"]))
        hard.extend(flags); extras.extend(found)
    decisive=[x for x in extras if x.get("confidence")=="high" and x.get("type")!="other"]
    special=_special_conditions(list(dict.fromkeys(hard)),decisive)
    if "субсид" in combined.casefold() and "СУБСИДИИ" not in special: special="; ".join(filter(None,[special,"СУБСИДИИ"]))
    item_results=[]
    for number,item in enumerate(lot.get("lotItems") or [],1):
        resolved = resolve_item_sources(item, docs, item_number=number, items=lot.get("lotItems") or [])
        reqs = resolved["requirements"]
        item_results.append({**resolved, "item_number":number,"item_name":item.get("description") or item.get("name") or item.get("eatTitle"),
                             "quantity":item.get("quantity"),"unit":item.get("okeiTitle") or (item.get("okei") or {}).get("title"),
                             "customer_unit_price":item.get("unitPrice"),"requirements":reqs,
                             "requirements_status":"Извлечено" if reqs else "Требуется ручная проверка ТЗ"})
    kinds=[kind for d in docs for kind in (d.get("document_type") or [])]
    price_present=any(x in {"price_justification","commercial_offer","price_list"} for x in kinds)
    model_candidates=sorted({x["price_justification_model"] for x in item_results if x.get("price_justification_model")})
    warnings=list(extraction["warnings"])
    if any(x["requirements_status"].startswith("Требуется") for x in item_results): warnings.append("Требуется ручная проверка ТЗ")
    audit_status="no_documents" if not extraction["documents_found"] else "partial" if extraction["documents_failed"] or extraction["extraction_summary"]["partial_documents"] else "analyzed"
    return {"procurement_id":extraction["procurement_id"],"procurement_number":raw.get("tradeNumber"),
            "audit_status":audit_status,"documents_analyzed":extraction["documents_processed"],
            "tz_status":"Извлечено по всем позициям" if item_results and all(x["requirements"] for x in item_results) else "Требуется ручная проверка ТЗ",
            "price_justification_status":"Обоснование найдено; не является главным ТЗ" if price_present else "Обоснование цены не найдено в локальных документах",
            "model_from_justification":{"status":"Только кандидат для проверки","candidates":model_candidates},
            "special_conditions":special,"additional_expenses":None,"inconsistencies":[],
            "document_warnings":list(dict.fromkeys(warnings)),"readiness_for_item_analysis":bool(item_results),
            "items":item_results,"input_evidence":{"documents_found":extraction["documents_found"],
                    "combined_text_length":len(combined),"document_hashes":[x.get("sha256") for x in docs if x.get("sha256")]}}
