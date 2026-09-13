import json
import tempfile
import unittest
from pathlib import Path

from pipeline.price_search_ready_batch import run
from suppliers.verification import PASSED


class PriceSearchReadyBatchTests(unittest.TestCase):
    def test_saved_ready_positions_run_without_eat_or_model_discovery(self):
        calls=[]
        def search(model,output_path):
            calls.append(model)
            return {'offers':[{'seller':f'shop-{len(calls)}','price':100+len(calls),
                'url':f'https://shop-{len(calls)}.ru/product/exact','availability':'В наличии',
                'exact_model_match':True}], 'candidate_discovery':{'candidate_count':1}}
        verifier=lambda row:{**row,'verification_status':PASSED}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result.json'
            result=run(search=search,verifier=verifier,output_path=path)
            saved=json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(calls),8)
        self.assertEqual(len(result['positions']),8)
        self.assertFalse(saved['eat_requested'])
        self.assertFalse(saved['model_discovery_performed'])

    def test_one_position_failure_does_not_stop_rest(self):
        count=0
        def search(model,output_path):
            nonlocal count
            count+=1
            if count==1:raise TimeoutError('source unavailable')
            return {'offers':[],'candidate_discovery':{'candidate_count':0}}
        with tempfile.TemporaryDirectory() as directory:
            result=run(search=search,output_path=Path(directory)/'result.json')
        self.assertEqual(len(result['positions']),8)
        self.assertIn('Ошибка позиции изолирована',result['positions'][0]['comments'])

    def test_saved_price_results_can_be_reused_without_search(self):
        def forbidden(*args,**kwargs):raise AssertionError('search must not run')
        # The project contains saved per-position price results from the live test.
        with tempfile.TemporaryDirectory() as directory:
            result=run(search=forbidden,use_saved_price_results=True,
                       output_path=Path(directory)/'result.json')
        self.assertEqual(len(result['positions']),8)


if __name__=='__main__':unittest.main()
