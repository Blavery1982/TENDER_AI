import unittest

from suppliers.price_search_flow import (
    build_price_search_result,
    confirmed_price_ranking,
    select_verified_top3,
)
from suppliers.verification import HIGH_RISK, MANUAL, PASSED


def offer(supplier, price, *, model=True, availability='В наличии'):
    return {'seller':supplier,'price':price,'url':f'https://{supplier}.ru/product/x',
            'exact_model_match':model,'availability':availability}


class PriceSearchFlowTests(unittest.TestCase):
    def test_ranking_uses_cheapest_actual_prices_and_direct_url(self):
        rows=confirmed_price_ranking([offer('high',300),offer('low',100),offer('mid',200)])
        self.assertEqual([x['public_price'] for x in rows],[100,200,300])
        self.assertEqual(rows[0]['product_url'],'https://low.ru/product/x')

    def test_exact_mismatch_and_unavailable_are_excluded(self):
        rows=confirmed_price_ranking([offer('wrong',1,model=False),
                                      offer('gone',2,availability='Нет в наличии'),offer('ok',3)])
        self.assertEqual([x['supplier_name'] for x in rows],['ok'])

    def test_antifraud_runs_after_price_sort_and_red_pulls_next(self):
        ranked=confirmed_price_ranking([offer('four',400),offer('two',200),
                                        offer('one',100),offer('three',300)])
        calls=[]
        def verifier(row):
            calls.append(row['public_price'])
            status=HIGH_RISK if row['supplier_name']=='one' else PASSED
            return {**row,'verification_status':status}
        top,history=select_verified_top3(ranked,verifier=verifier)
        self.assertEqual(calls,[100,200,300,400])
        self.assertEqual([x['public_price'] for x in top],[200,300,400])
        self.assertEqual(history[0]['verification_status'],HIGH_RISK)

    def test_yellow_supplier_is_kept(self):
        rows=confirmed_price_ranking([offer('yellow',100)])
        top,_=select_verified_top3(rows,verifier=lambda row:{**row,'verification_status':MANUAL})
        self.assertEqual(len(top),1)

    def test_top3_are_distinct_suppliers(self):
        duplicate={**offer('same',200),'url':'https://same.ru/product/y'}
        rows=confirmed_price_ranking([offer('same',100),duplicate,offer('two',300),offer('three',400)])
        top,_=select_verified_top3(rows,verifier=lambda row:{**row,'verification_status':PASSED})
        self.assertEqual([x['supplier_name'] for x in top],['same','two','three'])

    def test_marketplace_cards_use_actual_merchant_as_supplier_identity(self):
        a={**offer('one',100),'url':'https://market.yandex.ru/card/a/1'}
        b={**offer('two',200),'url':'https://market.yandex.ru/card/b/2'}
        rows=confirmed_price_ranking([a,b])
        self.assertEqual([x['supplier_name'] for x in rows],['one','two'])

    def test_economy_uses_offer1_and_public_price_is_not_purchase_price(self):
        prices={'offers':[offer('two',200),offer('one',100)],
                'candidate_discovery':{'candidate_count':5}}
        position={'procurement_id':'p','item_number':1,'item_name':'x','quantity':2,
                  'customer_unit_price':500,'exact_model':'X-1','model_source':'test'}
        result=build_price_search_result(position,prices,
            verifier=lambda row:{**row,'verification_status':PASSED},commission_rate=.03)
        self.assertEqual(result['offer_1']['price'],100)
        self.assertEqual(result['actual_prices_confirmed'],2)
        self.assertEqual(result['unique_suppliers_with_confirmed_price'],2)
        self.assertEqual(result['preliminary_economics']['public_total_for_quantity'],200)
        self.assertIsNone(result['preliminary_economics']['purchase_price'])

    def test_less_than_three_does_not_fail(self):
        prices={'offers':[offer('one',100)]}
        result=build_price_search_result({'quantity':1,'customer_unit_price':500},prices,
            verifier=lambda row:{**row,'verification_status':PASSED})
        self.assertEqual(result['status'],'ONLY_ONE_VALID_OFFER')
        self.assertIsNone(result['offer_2'])
        self.assertIn('МЕНЕЕ 3',result['comments'])


if __name__=='__main__':unittest.main()
