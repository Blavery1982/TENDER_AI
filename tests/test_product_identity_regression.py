import json
import unittest
from pathlib import Path
from model_search.product_evidence import extract_skus,html_page,page_matches,primary_content,candidates_from_results
from model_search.compliance import assess_model,parameter_key

class ProductIdentityRegression(unittest.TestCase):
    def test_power_pair_in_heading_is_not_a_competing_model(self):
        self.assertEqual(extract_skus('CyberPower PR1500ELCD 1500VA/1350W'), ['PR1500ELCD'])

    def test_real_models(self):
        for sku in ('RC-TWN28HN','EACS-09HP/N3','DC07RH','AS-09UW4RYRKB05','SAS09BN3-AI','SAU09BN3-AI-LE','AS-13UW4RXVQF00','UE32T5300','M283fdn','4303dw','SAS09BN3-AI/SAU09BN3-AI-LE'):
            with self.subTest(sku=sku):self.assertEqual(extract_skus('Модель: '+sku),[sku])
    def test_false_models(self):
        for value in ('holod-2','992товара','Hisense45','7-kvt-25-m2','Market777','753x308x189','10-летней','м3/час','Ф125мм','2026-09-11','2600W','+74951234567'):
            with self.subTest(value=value):self.assertEqual(extract_skus(value),[])
    def test_catalog_noise_does_not_fill_candidates(self):
        rows=[{'title':'Кондиционеры 992товара Hisense45','url':'https://shop.ru/holod-2'}, {'title':'Сплит-система Electrolux EACS-09HP/N3','url':'https://shop.ru/product'}]
        self.assertEqual([c['sku'] for c in candidates_from_results(rows)],['EACS-09HP/N3'])
    def test_h1_beats_recommendations(self):
        p=html_page('<h1>LG DC07RH</h1><div class="related"><h2>Hisense AS-09UW4RYRKB05</h2></div>','https://shop.ru/product')
        self.assertTrue(page_matches(p,'DC07RH'))
        self.assertFalse(page_matches(p,'AS-09UW4RYRKB05'))
    def test_wrong_primary_beats_body(self):
        p={'heading':'LG DC07RH','text':'EACS-09HP/N3\nЦвет: белый'}
        self.assertFalse(page_matches(p,'EACS-09HP/N3'))
    def test_title_beats_body(self):
        self.assertTrue(page_matches({'title':'LG DC07RH','text':'Рекомендуем EACS-09HP/N3'},'DC07RH'))
    def test_canonical_fallback(self):
        self.assertTrue(page_matches({'canonical_url':'https://shop.ru/DC07RH','text':'EACS-09HP/N3'},'DC07RH'))
    def test_structured_fallback(self):
        self.assertTrue(page_matches({'structured_products':[{'@type':'Product','mpn':'DC07RH'}],'text':'EACS-09HP/N3'},'DC07RH'))
    def test_ambiguous_document(self):
        self.assertFalse(page_matches({'content_kind':'document','text':'DC07RH EACS-09HP/N3'},'DC07RH'))
    def test_components_not_merged(self):
        self.assertFalse(page_matches({'heading':'RC-TWN28HN/IN'},'RC-TWN28HN'))
    def test_recommendation_fact_never_used(self):
        p=html_page('<h1>LG DC07RH</h1><div class="related"><h2>EACS-09HP/N3</h2><table><tr><td>Цвет</td><td>белый</td></tr></table></div>','https://shop.ru/product')
        self.assertEqual(assess_model([{'parameter':'Цвет','value':'белый'}],[p],'DC07RH')['requirements_check'][0]['result'],'could_not_confirm')
    def test_primary_table_nested_markup(self):
        p=html_page('<h1>LG DC07RH</h1><table><tr><th><span>Цвет</span></th><td><p>Белый</p></td></tr></table><footer>Цвет: черный</footer>','https://shop.ru/product')
        self.assertEqual(assess_model([{'parameter':'Цвет','value':'белый'}],[p],'DC07RH')['requirements_check'][0]['result'],'corresponds')
    def test_unmarked_recommendation_heading(self):
        p=html_page('<h1>LG DC07RH</h1><h2>Похожие товары</h2><p>Цвет: белый</p>','https://shop.ru/product')
        self.assertNotIn('Цвет:',primary_content(p))
    def test_div_attribute_rows(self):
        p=html_page('<h1>LG DC07RH</h1><div class="product-data__item"><div class="product-data__item-div">Тип компрессора</div><div class="product-data__item-div">inverter</div></div>','https://shop.ru/product')
        self.assertEqual(assess_model([{'parameter':'Инверторный тип','value':'Нет'}],[p],'DC07RH')['requirements_check'][0]['result'],'does_not_comply')
    def test_input_power_not_cooling_capacity(self):
        p={'heading':'DC07RH','text':'Потребляемая мощность охлаждения: 857 Вт'}
        r=assess_model([{'parameter':'Мощность в режиме охлаждения','value':'2.6 кВт','operator':'minimum'}],[p],'DC07RH')
        self.assertEqual(r['requirements_check'][0]['result'],'could_not_confirm')
    def test_saved_five_pages(self):
        root=Path(__file__).resolve().parents[1]/'data/yandex_model_search_control/20260911T110342708671Z'
        saved=json.loads((root/'supplemental_compliance.json').read_text())
        for i,c in enumerate(saved['candidates']):
            with self.subTest(sku=c['sku']):
                p=html_page((root/'saved_html'/f'{i}.html').read_text(),c['identity_source'])
                self.assertEqual(extract_skus(p['heading']),[c['sku']])
                self.assertTrue(page_matches(p,c['sku']))
                assessment=assess_model([{'parameter':'Инверторный тип кондиционера','value':'Нет'}],[p],c['sku'])
                self.assertEqual(assessment['requirements_check'][0]['result'],'corresponds' if i==0 else 'does_not_comply')

if __name__=='__main__':unittest.main()
