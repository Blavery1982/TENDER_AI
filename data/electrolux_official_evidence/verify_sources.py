import json
from pathlib import Path
from urllib.request import Request,urlopen
from concurrent.futures import ThreadPoolExecutor
from model_search.product_evidence import html_page,document_pages
root=Path(__file__).resolve().parent
urls=[('brand_site','https://www.home-comfort.ru/'),('sellers','https://electrolux-home.ru/info-about-sellers/'),('mirror','https://electrolux-home.ru/upload/iblock/ba4/xfgk2oxe7x07ru4e8lpkm9z0npptudxn/instr_elux_eacs_12_hp_n3.pdf')]
def fetch(e):
 name,url=e
 try:
  with urlopen(Request(url,headers={'User-Agent':'TENDER_AI/1.0 technical evidence'}),timeout=25) as r:raw=r.read(40000000);final=r.geturl()
  suffix='.pdf' if raw.startswith(b'%PDF') else '.html';path=root/(name+suffix);path.write_bytes(raw)
  pages=document_pages(path,final) if suffix=='.pdf' else [html_page(raw.decode('utf-8','replace'),final)]
  (root/(name+'.json')).write_text(json.dumps(pages,ensure_ascii=False,indent=2));print(name,len(raw),len(pages),final)
 except Exception as e:print(name,type(e).__name__)
with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(fetch,urls))
