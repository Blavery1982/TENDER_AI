import json
from pathlib import Path
from model_search.yandex_provider import YandexSearchProvider
out=Path(__file__).resolve().parent/'searches.json'
if out.exists():raise SystemExit('Saved search exists; no repeat allowed')
p=YandexSearchProvider(allow_paid=True,max_requests=2)
queries=['"Electrolux" "EACS-09HP/N3" официальный характеристики','"Electrolux" "EACS-09HP/N3" manual pdf']
try:
 for q in queries:
  rows=p.search(q,limit=8)
  out.write_text(json.dumps({'api_calls':p.api_calls,'searches':p.search_log},ensure_ascii=False,indent=2))
finally:
 out.write_text(json.dumps({'api_calls':p.api_calls,'searches':p.search_log},ensure_ascii=False,indent=2))
print(out.read_text())
