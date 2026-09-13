import json
from pathlib import Path
from urllib.request import Request,urlopen
from concurrent.futures import ThreadPoolExecutor
from model_search.product_evidence import html_page,document_pages
root=Path(__file__).resolve().parent
urls=[('manual','https://ecoclima.ru/erc/2022/08/16/896f51281d6a75266ad0c0193165542ad952c913.pdf'),('viewer','https://electrolux-home.ru/doc-view/84212/317568/'),('library','https://www.c-o-k.ru/library/instructions/electrolux/kondicionery-bytovye/33454')]
def fetch(entry):
 name,url=entry
 try:
  with urlopen(Request(url,headers={'User-Agent':'TENDER_AI/1.0 technical evidence'}),timeout=30) as r:raw=r.read(45000000)
  suffix='.pdf' if raw.startswith(b'%PDF') else '.html';path=root/(name+suffix);path.write_bytes(raw)
  pages=document_pages(path,url) if suffix=='.pdf' else [html_page(raw.decode('utf-8','replace'),url)]
  (root/(name+'.json')).write_text(json.dumps(pages,ensure_ascii=False,indent=2));print(name,len(raw),len(pages))
 except Exception as e:print(name,type(e).__name__)
with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(fetch,urls))
