"""Offline replay: only saved HTML and saved requirements; no network/credentials."""
import json
from pathlib import Path
from collections import Counter
from model_search.product_evidence import html_page,page_matches,extract_skus,primary_content,candidates_from_results
from model_search.live_discovery import normalize_requirements
from model_search.compliance import assess_model,facts
root=Path(__file__).resolve().parent
old=json.loads((root/'supplemental_compliance.json').read_text())
report=json.loads((root/'report.json').read_text())
requirements=normalize_requirements(report['requirements']);results=[]
for i,c in enumerate(old['candidates']):
 page=html_page((root/'saved_html'/f'{i}.html').read_text(),c['identity_source'])
 page.update(source_type='professional_store',source_verified=True)
 result={'brand':c['brand'],'sku':c['sku'],'source':c['identity_source'],'recognized_skus':extract_skus(page['heading']),'page_matches':page_matches(page,c['sku']),'extracted_facts':list(facts(primary_content(page))),**assess_model(requirements,[page],c['sku'])}
 result['counts']=dict(Counter(x['result'] for x in result['requirements_check']))
 results.append(result)
 print(c['sku'],result['recognized_skus'],result['page_matches'],result['counts'])
 print([(x['requirement'],x['found_value'],x['result']) for x in result['requirements_check'] if x['found_value'] is not None])
(root/'semantic_compliance.json').write_text(json.dumps({'new_api_requests':0,'candidates':results},ensure_ascii=False,indent=2))
