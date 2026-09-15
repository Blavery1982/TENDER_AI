import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from model_search.live_price_search import inspect_product_url,search_exact_model_prices,normalize_availability

PAGE='''<html><head><title>Royal Clima RC-TWN28HN</title><script type="application/ld+json">{"@type":"Product","name":"Royal Clima RC-TWN28HN","sku":"RC-TWN28HN","offers":{"price":"23850","priceCurrency":"RUB","availability":"https://schema.org/InStock"}}</script></head><body><h1>Royal Clima RC-TWN28HN</h1></body></html>'''
WRONG=PAGE.replace('RC-TWN28HN','RC-TWN28HN/IN')
def fetch(body=PAGE): return lambda url:(body,url,200)

class PriceSearchTests(unittest.TestCase):
 def test_availability_normalization_is_non_decisional(self):
  self.assertEqual(normalize_availability('В наличии'),'in_stock')
  self.assertEqual(normalize_availability('Под заказ'),'order')
  self.assertEqual(normalize_availability('Нет в наличии'),'out_of_stock')
  self.assertEqual(normalize_availability(None),'unknown')

 def test_exact_model_offer(self):
  offer=inspect_product_url({'product_url':'https://shop.ru/x','supplier_name':'Shop'},'RC-TWN28HN',fetcher=fetch())
  self.assertEqual(offer['price'],23850);self.assertIs(offer['exact_model_match'],True)
  self.assertEqual(offer['confirmed_price'],23850)
  self.assertEqual(offer['source_url'],'https://shop.ru/x')
  self.assertEqual(offer['domain'],'shop.ru')
  self.assertEqual(offer['extraction_method'],'json_ld')
  self.assertEqual(offer['availability_normalized'],'in_stock')
  self.assertEqual(offer['availability_raw'],'https://schema.org/InStock')

 def test_html_price_and_order_availability_are_preserved(self):
  page='''<html><head><title>Royal Clima RC-TWN28HN</title></head><body>
  <h1>Royal Clima RC-TWN28HN</h1><p>Цена: 23850 ₽</p><p>Под заказ</p></body></html>'''
  offer=inspect_product_url({'product_url':'https://shop.ru/product/x'},'RC-TWN28HN',fetcher=fetch(page))
  self.assertEqual(offer['confirmed_price'],23850)
  self.assertEqual(offer['extraction_method'],'html')
  self.assertEqual(offer['availability_raw'],'Под заказ')
  self.assertEqual(offer['availability_normalized'],'order')
 def test_sparse_supplier_card_is_valid_price_evidence_for_exact_sku(self):
  sparse='''<html><head><title>Royal Clima RC-TWN28HN</title><script type="application/ld+json">{"@type":"Product","name":"Royal Clima RC-TWN28HN","sku":"RC-TWN28HN","offers":{"price":"23850","priceCurrency":"RUB","availability":"https://schema.org/InStock"}}</script></head><body><h1>Royal Clima RC-TWN28HN</h1><p>Настенный; белый; 2.65 кВт</p></body></html>'''
  offer=inspect_product_url({'product_url':'https://shop.ru/x','supplier_name':'Shop'},
                            'RC-TWN28HN',fetcher=fetch(sparse))
  self.assertEqual(offer['price'],23850)
  self.assertTrue(offer['exact_model_match'])
 def test_similar_sku_rejected(self): self.assertIsNone(inspect_product_url({'product_url':'https://shop.ru/x'},'RC-TWN28HN',fetcher=fetch(WRONG)))
 def test_fields(self):
  x=inspect_product_url({'product_url':'https://shop.ru/x','supplier_name':'Shop'},'RC-TWN28HN',fetcher=fetch());
  for k in ('exact_product_name','price','availability','seller','url','source','checked_at'): self.assertIn(k,x)
 def test_failure_isolated(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'s.json';p.write_text(json.dumps({'offers':[{'model':'RC-TWN28HN','product_url':'https://bad.ru/product/x','supplier_name':'Bad'},{'model':'RC-TWN28HN','product_url':'https://ok.ru/product/x','supplier_name':'OK'}]}))
   def f(url):
    if 'bad.ru' in url: raise TimeoutError('x')
    if 'market.yandex' in url:return ('RC-TWN28HN',url,200)
    return PAGE,url,200
   r=search_exact_model_prices('RC-TWN28HN',seeds_path=p,output_path=Path(d)/'o.json',fetcher=f,pause=0,automatic_discovery=False)
   self.assertEqual(r['offers_found'],1);self.assertEqual(len(r['source_errors']),1)
 def test_minimum(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'s.json';p.write_text(json.dumps({'offers':[{'model':'RC-TWN28HN','product_url':'https://ok.ru/product/x','supplier_name':'OK'}]}))
   r=search_exact_model_prices('RC-TWN28HN',seeds_path=p,output_path=Path(d)/'o.json',fetcher=fetch(),pause=0,automatic_discovery=False)
   self.assertEqual(r['minimum_price'],23850)

 def test_exact_price_search_receives_full_customer_designation(self):
  model='Bergauf Keramik PLUS 25 кг'
  with tempfile.TemporaryDirectory() as d, patch('model_search.live_price_search.discover_candidate_urls',
                                                  return_value={'candidate_urls': [], 'candidate_count': 0,
                                                                'source_reports': []}) as discovery:
   result=search_exact_model_prices(model,output_path=Path(d)/'o.json',fetcher=fetch(),pause=0,
                                    include_saved_seeds=False)
   self.assertEqual(result['target_model'],model)
   self.assertEqual(discovery.call_args.args[0],model)

 def test_product_name_can_identify_sku_when_schema_model_is_internal(self):
  page=PAGE.replace('"sku":"RC-TWN28HN",','"model":"internal-22786",')
  self.assertEqual(inspect_product_url({'product_url':'https://shop.ru/x'},'Royal Clima RC-TWN28HN',fetcher=fetch(page))['price'],23850)

 def test_supplier_internal_sku_does_not_block_manufacturer_model(self):
  page=PAGE.replace('"sku":"RC-TWN28HN"','"sku":"STORE-998877"')
  result=inspect_product_url({'product_url':'https://shop.ru/x'},
                             'Royal Clima RC-TWN28HN',fetcher=fetch(page))
  self.assertEqual(result['price'],23850)
  self.assertEqual(result['exact_manufacturer_model'],'RC-TWN28HN')
  self.assertEqual(result['supplier_sku'],'STORE-998877')

 def test_manufacturer_model_formatting_variants(self):
  variants=['Electrolux EACS-09HP/N3','EACS-09HP/N3 Electrolux',
            'ELECTROLUX EACS-09HP/N3','Electrolux EACS 09HP/N3, белый']
  for title in variants:
   page=PAGE.replace('Royal Clima RC-TWN28HN',title).replace('RC-TWN28HN', 'EACS-09HP/N3')
   self.assertIsNotNone(inspect_product_url({'product_url':'https://shop.ru/x'},
                                            'Electrolux EACS-09HP/N3',fetcher=fetch(page)),title)

 def test_neighbor_manufacturer_models_remain_different(self):
  page=PAGE.replace('Royal Clima RC-TWN28HN','Electrolux EACS-12HP/N3').replace('RC-TWN28HN','EACS-12HP/N3')
  self.assertIsNone(inspect_product_url({'product_url':'https://shop.ru/x'},
                                        'Electrolux EACS-09HP/N3',fetcher=fetch(page)))
  page=PAGE.replace('Royal Clima RC-TWN28HN','HP M283fdw').replace('RC-TWN28HN','M283fdw')
  self.assertIsNone(inspect_product_url({'product_url':'https://shop.ru/x'},
                                        'HP M283fdn',fetcher=fetch(page)))

 def test_search_checks_more_than_first_three_candidates(self):
  with tempfile.TemporaryDirectory() as d:
   rows=[{'model':'RC-TWN28HN','product_url':f'https://shop{i}.ru/item','supplier_name':f'Shop{i}'} for i in range(5)]
   p=Path(d)/'s.json';p.write_text(json.dumps({'offers':rows}))
   r=search_exact_model_prices('RC-TWN28HN',seeds_path=p,output_path=Path(d)/'o.json',
                               fetcher=fetch(),pause=0,automatic_discovery=False)
   self.assertEqual(r['offers_found'],5)

 def test_snippet_price_never_replaces_product_page_price(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'s.json';p.write_text(json.dumps({'offers':[{
       'model':'RC-TWN28HN','product_url':'https://shop.ru/item',
       'supplier_name':'Shop','snippet_price':15000}]}))
   page=PAGE.replace('23850','18900')
   r=search_exact_model_prices('RC-TWN28HN',seeds_path=p,output_path=Path(d)/'o.json',
                               fetcher=fetch(page),pause=0,automatic_discovery=False)
   self.assertEqual(r['minimum_price'],18900)

 def test_category_url_is_not_a_direct_product_offer(self):
  self.assertIsNone(inspect_product_url({'product_url':'https://market.yandex.ru/category/fuji-1'},
                                        'Fuji 1',fetcher=fetch()))

 def test_accessory_is_not_a_product_offer(self):
  accessory=PAGE.replace('<h1>Royal Clima RC-TWN28HN</h1>', '<title>Картридж Royal Clima RC-TWN28HN</title><h1>Картридж Royal Clima RC-TWN28HN</h1>')
  self.assertIsNone(inspect_product_url({'product_url':'https://shop.ru/x'},'RC-TWN28HN',fetcher=fetch(accessory)))

 def test_market_card_uses_actual_merchant_name(self):
  page=PAGE.replace('</body>','<script>{"supplierName":"Мир стоматологов"}</script></body>')
  row={'product_url':'https://market.yandex.ru/card/gidrogum-5/1','supplier_name':'Яндекс Маркет'}
  result=inspect_product_url(row,'RC-TWN28HN',fetcher=fetch(page))
  self.assertEqual(result['seller'],'Мир стоматологов')

if __name__=='__main__':unittest.main()
