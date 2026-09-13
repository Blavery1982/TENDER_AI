import tempfile
import unittest
from pathlib import Path

from pipeline.second_queue import run_second_queue_position
from pipeline import priority_production, second_queue
from suppliers.verification import PASSED


def candidate(model,status='fully_compliant',result='corresponds',source='https://maker.ru'):
    return {'exact_model':model,'sku':model,'technical_status':status,'status':status,
            'requirements_check':[{'requirement':'Мощность','required_value':'2.6 кВт',
                                   'found_value':'2.8 кВт','result':result,'source':source}],
            'source_urls':[source]}


def item():
    return {'name':'Кондиционер','item_name':'Кондиционер','item_number':1,'quantity':3,
            'customer_unit_price':25400,'requirements':[{'parameter':'Мощность охлаждения',
                                                         'value':'2.6 кВт','operator':'minimum'}]}


def prices(model,price):
    return {'offers':[{'seller':model+' shop','price':price,
        'url':'https://'+model.lower()+'.ru/product/exact','availability':'В наличии',
        'exact_model_match':True}], 'candidate_discovery':{'candidate_count':5}}


class SecondQueueTests(unittest.TestCase):
    def execute(self,candidates,market=None,provider=None,market_rows=None):
        captured={}
        def discovery(source,**kwargs):
            captured.update(kwargs)
            return {'candidates':candidates,'queries_used':['q1','q2'],
                    'live_queries_count':2}
        market=market or {c['exact_model']:10000 for c in candidates}
        calls=[]
        def search(model,output_path):
            calls.append(model)
            if market_rows and model in market_rows:
                return {'offers':market_rows[model],
                        'candidate_discovery':{'candidate_count':len(market_rows[model])}}
            return prices(model,market[model])
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(item(),procurement_id='p',discoverer=discovery,
                provider=provider,price_search=search,
                verifier=lambda row:{**row,'verification_status':PASSED},
                output_path=Path(directory)/'result.json',cache_dir=Path(directory)/'cache')
        return result,captured,calls

    def test_model_discovery_required_enters_second_queue(self):
        result,captured,_=self.execute([candidate('A-100')])
        self.assertEqual(result['status'],'SELECTED_MODEL_PRICE_SEARCH_COMPLETED')
        self.assertEqual(captured['query_limit'],3)
        self.assertEqual(captured['candidate_limit'],10)

    def test_mismatch_is_rejected(self):
        bad=candidate('A-100','non_compliant','does_not_comply')
        result,_,_=self.execute([bad])
        self.assertEqual(result['status'],'MODEL_NOT_CONFIRMED')
        self.assertEqual(result['rejected_models'][0]['model'],'A-100')

    def test_unknown_is_not_fully_compliant(self):
        unknown=candidate('A-100','not_confirmed','could_not_confirm')
        result,_,_=self.execute([unknown])
        self.assertFalse(result['fully_compliant_models'])
        self.assertIsNone(result['selected_model'])

    def test_all_corresponds_is_fully_compliant(self):
        result,_,_=self.execute([candidate('A-100')])
        self.assertEqual(len(result['fully_compliant_models']),1)

    def test_multiple_models_use_rough_price_ranking(self):
        result,_,calls=self.execute([candidate('A-100'),candidate('B-200')],
                                {'A-100':30000,'B-200':21000})
        self.assertEqual(result['selected_model'],'B-200')
        self.assertEqual(calls,['A-100','B-200'])
        self.assertEqual(result['rough_price_per_model'][0]['model'],'B-200')

    def test_selected_model_reuses_existing_first_queue_result(self):
        result,_,calls=self.execute([candidate('A-100')])
        self.assertEqual(calls,['A-100'])
        self.assertEqual(result['downstream_price_search_result']['offer_1']['price'],10000)
        self.assertFalse(result['ai_provider_used'])
        self.assertFalse(result['compliance_repeated_during_price_search'])

    def test_no_confirmed_price_is_manual_without_crash(self):
        def discovery(source,**kwargs):
            return {'candidates':[candidate('A-100')],'queries_used':['q1'],'live_queries_count':1}
        def search(model,output_path):return {'offers':[],'candidate_discovery':{'candidate_count':0}}
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(item(),procurement_id='p',discoverer=discovery,
                price_search=search,output_path=Path(directory)/'r.json',cache_dir=Path(directory)/'c')
        self.assertEqual(result['status'],'SELECTED_MODEL_PRICE_SEARCH_COMPLETED')
        self.assertEqual(result['selected_model'],'A-100')
        self.assertEqual(result['price_search_offer_count'],0)

    def test_provider_call_count_is_preserved_and_bounded(self):
        class Provider:api_calls=3
        result,_,_=self.execute([candidate('A-100')],provider=Provider())
        self.assertEqual(result['yandex_api_calls'],3)

    def test_both_queues_share_the_same_exact_model_price_search(self):
        self.assertIs(second_queue.search_exact_model_prices,
                      priority_production.search_exact_model_prices)

    def test_one_suspiciously_cheap_card_does_not_automatically_win(self):
        def row(model,seller,price):
            return {'seller':seller,'price':price,'url':f'https://{seller}.ru/{model}',
                    'availability':'В наличии','exact_model_match':True}
        markets={
            'A-100':[row('A-100','only',10000)],
            'B-200':[row('B-200','one',11000),row('B-200','two',11200)],
        }
        result,_,_=self.execute([candidate('A-100'),candidate('B-200')],market_rows=markets)
        self.assertEqual(result['selected_model'],'B-200')
        self.assertEqual(result['rough_price_per_model'][0]['market_signal_quality'],'stable')

    def test_price_justification_characteristic_is_not_customer_requirement(self):
        source=item()
        source['price_justification_requirements']=[{'parameter':'Шум','value':'18 дБ'}]
        result,_,_=self.execute([candidate('A-100')])
        self.assertEqual(len(result['requirements']),1)
        self.assertEqual(result['selected_model'],'A-100')

    def test_dynamic_four_of_four_requirements_are_enough(self):
        source=item()
        source['requirements']=[{'parameter':f'Параметр {n}','value':'да'} for n in range(4)]
        complete=candidate('A-100')
        complete['requirements_check']=[{'requirement':f'Параметр {n}','required_value':'да',
            'found_value':'да','result':'corresponds','source':f'https://source{n}.ru/a'} for n in range(4)]
        def discovery(_source,**kwargs):return {'candidates':[complete],'queries_used':[],'live_queries_count':0}
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(source,procurement_id='p',discoverer=discovery,
                price_search=lambda model,output_path:prices(model,10000),
                verifier=lambda row:{**row,'verification_status':PASSED},
                output_path=Path(directory)/'r.json',cache_dir=Path(directory)/'c')
        self.assertEqual(result['selected_model'],'A-100')
        self.assertEqual(len(result['selected_model_evidence']),4)

    def test_winner_alone_enters_shared_full_price_flow(self):
        rough_calls=[];full_calls=[]
        def rough(model,output_path):
            rough_calls.append(model)
            return prices(model,30000 if model=='A-100' else 20000)
        def full(model,output_path):
            full_calls.append(model)
            return prices(model,19000)
        def discovery(_source,**kwargs):
            return {'candidates':[candidate('A-100'),candidate('B-200')],
                    'queries_used':[],'live_queries_count':0}
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(item(),procurement_id='p',discoverer=discovery,
                rough_price_search=rough,price_search=full,
                verifier=lambda row:{**row,'verification_status':PASSED},
                output_path=Path(directory)/'r.json',cache_dir=Path(directory)/'c')
        self.assertEqual(rough_calls,['A-100','B-200'])
        self.assertEqual(full_calls,['B-200'])
        self.assertEqual(result['downstream_price_search_result']['offer_1']['price'],19000)

    def test_likely_candidate_enters_rough_ranking_and_price_search_with_review(self):
        source=item();source['requirements'].extend([
            {'parameter':'Тип товара','value':'Сплит-система'},
            {'parameter':'Гарантия','value':'2 года','criticality':'secondary'}])
        likely=candidate('A-100')
        likely['requirements_check'].extend([
            {'requirement':'Тип товара','result':'corresponds',
             'found_value':'Сплит-система','source':'https://maker.ru'},
            {'requirement':'Гарантия','result':'could_not_confirm',
             'found_value':None,'source':None}])
        def discovery(_source,**kwargs):
            return {'candidates':[likely],'queries_used':[],'live_queries_count':0}
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(source,procurement_id='p',discoverer=discovery,
                price_search=lambda model,output_path:prices(model,10000),
                verifier=lambda row:{**row,'verification_status':PASSED},
                output_path=Path(directory)/'r.json',cache_dir=Path(directory)/'c')
        self.assertEqual(result['selected_model'],'A-100')
        self.assertEqual(result['selected_model_admission_status'],'LIKELY_COMPLIANT')
        self.assertEqual(result['status'],'MODEL_SELECTION_REQUIRES_REVIEW')
        self.assertEqual(result['rough_price_per_model'][0]['model'],'A-100')

    def test_rejected_candidate_never_enters_rough_ranking(self):
        bad=candidate('A-100','non_compliant','does_not_comply')
        result,_,calls=self.execute([bad])
        self.assertEqual(result['rough_price_per_model'],[])
        self.assertEqual(calls,[])

    def test_confirmed_wins_equal_price_over_likely(self):
        source=item();source['requirements'].append({
            'parameter':'Гарантия','value':'2 года','criticality':'secondary'})
        confirmed=candidate('A-100');confirmed['requirements_check'].append({
            'requirement':'Гарантия','result':'corresponds','found_value':'2 года','source':'https://maker.ru'})
        likely=candidate('B-200');likely['requirements_check'].append({
            'requirement':'Гарантия','result':'could_not_confirm','found_value':None,'source':None})
        def discovery(_source,**kwargs):
            return {'candidates':[likely,confirmed],'queries_used':[],'live_queries_count':0}
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(source,procurement_id='p',discoverer=discovery,
                price_search=lambda model,output_path:prices(model,10000),
                verifier=lambda row:{**row,'verification_status':PASSED},
                output_path=Path(directory)/'r.json',cache_dir=Path(directory)/'c')
        self.assertEqual(result['selected_model'],'A-100')
        self.assertEqual(result['selected_model_admission_status'],'CONFIRMED_COMPLIANT')

    def test_every_mandatory_requirement_must_be_confirmed(self):
        source=item();source['requirements'].append({'parameter':'Цвет','value':'белый'})
        incomplete=candidate('A-100')
        def discovery(_source,**kwargs):
            return {'candidates':[incomplete],'queries_used':[],'live_queries_count':0}
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(source,procurement_id='p',discoverer=discovery,
                price_search=lambda *args,**kwargs:prices('A-100',10000),
                output_path=Path(directory)/'r.json',cache_dir=Path(directory)/'c')
        self.assertIsNone(result['selected_model'])

    def test_evidence_for_one_sku_may_come_from_multiple_sources(self):
        source=item();source['requirements'].append({'parameter':'Цвет','value':'белый'})
        complete=candidate('A-100')
        complete['requirements_check'].append({
            'requirement':'Цвет','required_value':'белый','found_value':'белый',
            'result':'corresponds','source':'https://manual.maker.ru/a-100.pdf'})
        complete['source_urls'].append('https://manual.maker.ru/a-100.pdf')
        def discovery(_source,**kwargs):
            return {'candidates':[complete],'queries_used':[],'live_queries_count':0}
        calls=[]
        with tempfile.TemporaryDirectory() as directory:
            result=run_second_queue_position(source,procurement_id='p',discoverer=discovery,
                price_search=lambda model,output_path:(calls.append(model) or prices(model,10000)),
                verifier=lambda row:{**row,'verification_status':PASSED},
                output_path=Path(directory)/'r.json',cache_dir=Path(directory)/'c')
        self.assertEqual(result['selected_model'],'A-100')
        self.assertEqual({row['source'] for row in result['selected_model_evidence']},
                         {'https://maker.ru','https://manual.maker.ru/a-100.pdf'})
        self.assertEqual(calls,['A-100'])


if __name__=='__main__':unittest.main()
