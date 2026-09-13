import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request,urlopen
root=Path(__file__).resolve().parent
saved=json.loads((root/'supplemental_compliance.json').read_text())
out=root/'saved_html';out.mkdir(exist_ok=True)
def load(pair):
 i,c=pair
 with urlopen(Request(c['identity_source'],headers={'User-Agent':'TENDER_AI/1.0 local model research'}),timeout=20) as response:
  raw=response.read(10_000_001)
  if len(raw)>10_000_000:raise ValueError('size limit')
  html=raw.decode(response.headers.get_content_charset() or 'utf-8','replace')
 (out/f'{i}.html').write_text(html,encoding='utf-8')
 print(i,c['sku'],'saved',len(raw))
with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(load,enumerate(saved['candidates'])))
