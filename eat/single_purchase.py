"""Точечное получение одной карточки ЕАТ через ручную браузерную сессию."""
from __future__ import annotations
import argparse
import hashlib, json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from playwright.sync_api import sync_playwright
from eat.browser_auth import BrowserAuthError
from eat.browser_session import _sanitize
from eat.browser_policy import open_authorized_eat_browser
from eat.card_payload import card_purchase_payloads, fullest_card_purchase
from eat.additional_characteristics import read_additional_characteristics
from eat.contract_analysis import _documents, _file_url, _safe_name
from documents.brand_detector import detect_brands, save_brand_audit
from documents.pipeline import process_procurement_documents
from documents.tender_archive import (archive_metadata, tender_folder,
                                       sha256_file, write_json)

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_PURCHASE_ID="31f18b8a-d3b9-4cd8-890c-5d11824fc635"
SUPPORTED={".pdf",".docx",".doc",".docm",".xlsx",".xls",".xlsm",".csv",".rtf",".txt",".odt",".ods",".zip",".rar",".7z",".png",".jpg",".jpeg"}

def fetch_purchase_card(context: Any, page: Any, purchase_id: str,
                        *, wait_ms: int = 8000,
                        analyze_documents: bool = True,
                        download_documents: bool = True) -> dict[str, Any]:
    """Получить карточку и документы через уже открытую общую EAT-сессию."""
    card=f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info"
    output=ROOT/f"data/eat_single_{purchase_id}.json"
    payloads=[]; docs=[]; auth_failed=[]
    def response_seen(response):
        if response.status in (401, 403) and purchase_id in response.url:
            auth_failed.append(True)
        ctype=response.headers.get("content-type","").casefold()
        if "json" in ctype:
            try:
                value=response.json(); payloads.extend(card_purchase_payloads(value, purchase_id)); docs.extend(_documents(value,response.url))
            except Exception:pass
        elif (response.headers.get("content-disposition") or
              Path(urlsplit(response.url).path).suffix.casefold() in SUPPORTED):
            docs.append({"file_name":Path(urlsplit(response.url).path).name or "document",
                         "document_type":None,"file_id":None,"download_url":response.url})
    page.on("response",response_seen)
    try:
        page.goto(card,wait_until="domcontentloaded",timeout=60000)
        page.wait_for_timeout(wait_ms)
        for anchor in page.locator("a[href]").all():
            try:
                href=anchor.get_attribute("href"); label=anchor.inner_text(timeout=500).strip()
                download_attr=anchor.get_attribute("download")
                label_probe = label.casefold()
                is_document_label = any(token in label_probe for token in (
                    "тз", "техническ", "документ", "приложен", "контракт", "договор",
                    "коммерческ", "обоснован", "спецификац", "файл", "кп",
                ))
                if href and (Path(urlsplit(href).path).suffix.casefold() in SUPPORTED or download_attr or is_document_label):
                    docs.append({"file_name":label or Path(urlsplit(href).path).name,"document_type":None,"file_id":None,"download_url":urljoin(page.url,href)})
            except Exception:pass
        unique={str(d.get("file_id") or d.get("download_url") or d.get("file_name")):d for d in docs}
        raw=fullest_card_purchase(payloads)
        if not raw and (auth_failed or urlsplit(page.url).path.startswith(("/login", "/auth"))):
            raise BrowserAuthError("Доступ к карточкам ЕАТ требует восстановления авторизации")
        lot = raw.get('lot') or raw
        popup_sources = read_additional_characteristics(page, lot.get('lotItems') or [], card)
        folder=tender_folder(purchase_id, tender_number=raw.get("tradeNumber"))
        files = [{**doc, "local_path": None, "download_status": "deferred"}
                 for doc in unique.values()]
        result={"purchase_id":purchase_id,"card_url":card,"raw":_sanitize(raw),"documents":_sanitize(files),
                "eat_popup_sources": popup_sources}
        if download_documents:
            files = download_purchase_documents(context, result)
            result["documents"] = files
        if not download_documents:
            archive_metadata(purchase_id, tender_number=(raw or {}).get("tradeNumber"),
                             title=(raw or {}).get("subject") or (raw or {}).get("title"),
                             card_url=card, documents=result["documents"], raw=result["raw"])
        if analyze_documents and download_documents:
            extraction=process_procurement_documents(result, result["documents"])
            write_json(folder / "document_extraction.json", extraction)
            brand_audit=detect_brands(result, extraction)
            save_brand_audit(result, brand_audit)
            result["document_extraction"]={k:v for k,v in extraction.items() if k != "combined_text"}
            result["brand_audit"]=brand_audit
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"Карточка сохранена: {output}; документов: {len(files)}",flush=True)
        return result
    finally:
        page.remove_listener("response", response_seen)


def document_for_route(doc, purpose):
    """Выбирать только документы, нужные для текущего этапа."""
    kind = doc.get("document_type")
    label = str(doc.get("file_name") or doc.get("document_name") or "").casefold()
    contract = str(kind) == "15" or any(x in label for x in ("контракт", "договор"))
    if purpose == "contract":
        return contract
    return contract or str(kind) == "14" or any(x in label for x in (
        "обоснован", "нмцк", "коммерческ", "спецификац", "техническ", "тз", "кп"))


def download_purchase_documents(context, card, *, purpose="all"):
    """Отложенная загрузка в прежний архив; остальные файлы сохраняют deferred."""
    raw = card.get("raw") or {}
    pid = card["purchase_id"]
    folder = tender_folder(pid, tender_number=raw.get("tradeNumber"))
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for source in card.get("documents") or []:
        doc = dict(source)
        if purpose != "all" and not document_for_route(doc, purpose):
            files.append(doc)
            continue
        if doc.get("download_status") == "downloaded" and Path(doc.get("local_path") or "").is_file():
            files.append(doc)
            continue
        if not doc.get("download_url") and doc.get("file_id") and doc.get("document_type") is not None:
            doc["download_url"] = _file_url(pid, doc["document_type"], doc["file_id"])
        url = doc.get("download_url")
        if not url:
            files.append({**doc, "local_path": None, "download_status": "no_url"})
            continue
        try:
            response = context.request.get(url, timeout=25000)
            if not response.ok:
                files.append({**doc, "local_path": None, "download_status": f"http_{response.status}"})
                continue
            name = _safe_name(doc.get("file_name") or Path(urlsplit(url).path).name)
            path = folder / name
            body = response.body()
            digest = hashlib.sha256(body).hexdigest()
            if not path.exists() or sha256_file(path) != digest:
                path.write_bytes(body)
            files.append({**doc, "file_name": name, "local_path": str(path), "sha256": digest,
                          "size_bytes": len(body), "download_status": "downloaded"})
        except Exception as exc:
            files.append({**doc, "local_path": None, "download_status": "error",
                          "error": type(exc).__name__})
    card["documents"] = files
    archive_metadata(pid, tender_number=raw.get("tradeNumber"), card_url=card.get("card_url"),
                     documents=files, raw=raw)
    return files


def run(purchase_id: str = DEFAULT_PURCHASE_ID)->int:
    with sync_playwright() as pw:
        session=open_authorized_eat_browser(pw)
        try:
            fetch_purchase_card(session.context, session.page, purchase_id)
        finally:
            session.close()
    return 0

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("purchase_id",nargs="?",default=DEFAULT_PURCHASE_ID)
    args=parser.parse_args()
    raise SystemExit(run(args.purchase_id))
