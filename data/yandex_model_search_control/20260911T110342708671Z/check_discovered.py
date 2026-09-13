"""Supplemental assessment of five real identities from the saved search results. No search API."""
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from model_search.live_discovery import WebProvider, normalize_requirements
from model_search.compliance import assess_model
from model_search.product_evidence import page_matches
root=Path(__file__).resolve().parent
report=json.loads((root/'report.json').read_text())
rows=report['searches'][1]['results']
identities=[(0,'Electrolux','EACS-09HP/N3'),(1,'LG','DC07RH'),(3,'Hisense','AS-09UW4RYRKB05'),(4,'Energolux','SAS09BN3-AI/SAU09BN3-AI-LE'),(5,'Hisense','AS-13UW4RXVQF00')]
requirements=normalize_requirements(report['requirements'])
def check(entry):
 i,brand,sku=entry;url=rows[i]['url'];pages=[];error=None
 try:
  fetched=WebProvider(retries=0).fetch(url)
  pages=fetched if isinstance(fetched,list) else [fetched]
 except Exception as exc:error=type(exc).__name__
 return {'brand':brand,'sku':sku,'identity_source':url,'identity_basis':'saved_search_title_or_product_url','pages':pages,'fetch_error':error,'matching_pages':sum(page_matches(p,sku) for p in pages),**assess_model(requirements,pages,sku)}
with ThreadPoolExecutor(max_workers=3) as pool: candidates=list(pool.map(check,identities))
out={'additional_yandex_requests':0,'total_yandex_requests':report['api_calls'],'assessment_kind':'supplemental; original automatic result retained unchanged','candidates':candidates,'selected_model':None}
(root/'supplemental_compliance.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
for c in candidates:
 print(c['brand'],c['sku'],c['status'],'pages',len(c['pages']),'matching',c['matching_pages'],'error',c['fetch_error'])
 print([(x['requirement'],x['result']) for x in c['requirements_check']])
