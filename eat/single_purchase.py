"""Точечное получение одной карточки ЕАТ через ручную браузерную сессию."""
from __future__ import annotations
import argparse
import hashlib, json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from playwright.sync_api import sync_playwright
from eat.browser_session import _sanitize
from eat.browser_policy import open_authorized_eat_browser
from eat.card_payload import card_purchase_payloads, fullest_card_purchase
from eat.contract_analysis import _documents, _file_url, _safe_name

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_PURCHASE_ID="31f18b8a-d3b9-4cd8-890c-5d11824fc635"
SUPPORTED={".pdf",".docx",".doc",".xlsx"}

def fetch_purchase_card(context: Any, page: Any, purchase_id: str,
                        *, wait_ms: int = 8000) -> dict[str, Any]:
    """Получить карточку и документы через уже открытую общую EAT-сессию."""
    card=f"https://agregatoreat.ru/purchases/announcement/{purchase_id}/info"
    output=ROOT/f"data/eat_single_{purchase_id}.json"
    folder=ROOT/f"data/contracts/{purchase_id}"
    payloads=[]; docs=[]
    def response_seen(response):
        ctype=response.headers.get("content-type","").casefold()
        if "json" not in ctype:return
        try:
            value=response.json(); payloads.extend(card_purchase_payloads(value, purchase_id)); docs.extend(_documents(value,response.url))
        except Exception:pass
    page.on("response",response_seen)
    try:
        page.goto(card,wait_until="domcontentloaded",timeout=60000)
        page.goto(card,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(wait_ms)
        for anchor in page.locator("a[href]").all():
            try:
                href=anchor.get_attribute("href"); label=anchor.inner_text(timeout=500).strip()
                if href and Path(urlsplit(href).path).suffix.casefold() in SUPPORTED:
                    docs.append({"file_name":label or Path(urlsplit(href).path).name,"document_type":None,"file_id":None,"download_url":urljoin(page.url,href)})
            except Exception:pass
        unique={str(d.get("file_id") or d.get("download_url") or d.get("file_name")):d for d in docs}
        files=[]; folder.mkdir(parents=True,exist_ok=True)
        for doc in unique.values():
            if not doc.get("download_url") and doc.get("file_id") and doc.get("document_type") is not None:
                doc["download_url"]=_file_url(purchase_id,doc["document_type"],doc["file_id"])
            url=doc.get("download_url")
            if not url: files.append({**doc,"local_path":None,"download_status":"no_url"}); continue
            response=context.request.get(url)
            if not response.ok: files.append({**doc,"local_path":None,"download_status":f"http_{response.status}"}); continue
            name=_safe_name(doc.get("file_name") or Path(urlsplit(url).path).name)
            path=folder/name; body=response.body()
            if not path.exists() or hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(body).digest():path.write_bytes(body)
            files.append({**doc,"local_path":str(path),"download_status":"downloaded"})
        raw=fullest_card_purchase(payloads)
        result={"purchase_id":purchase_id,"card_url":card,"raw":_sanitize(raw),"documents":_sanitize(files)}
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"Карточка сохранена: {output}; документов: {len(files)}",flush=True)
        return result
    finally:
        page.remove_listener("response", response_seen)


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
