import unittest
from model_search.candidate_discovery import discover_candidate_urls,extract_candidate_links

class CandidateDiscoveryTests(unittest.TestCase):
 def test_exact_link(self):
  body='<a href="/product/royal-clima-rc-twn28hn">Royal Clima RC-TWN28HN</a>'
  self.assertEqual(extract_candidate_links(body,'https://shop.ru/search','shop.ru','RC-TWN28HN'),['https://shop.ru/product/royal-clima-rc-twn28hn'])
 def test_similar_sku_excluded(self):
  body='<a href="/product/rc-twn28hn-in">RC-TWN28HN/IN</a><a href="/product/rc-gl28hn">RC-GL28HN</a>'
  self.assertEqual(extract_candidate_links(body,'https://shop.ru','shop.ru','RC-TWN28HN'),[])
 def test_external_domain_excluded(self):
  body='<a href="https://evil.ru/RC-TWN28HN">RC-TWN28HN</a>'
  self.assertEqual(extract_candidate_links(body,'https://shop.ru','shop.ru','RC-TWN28HN'),[])
 def test_source_failure_isolated(self):
  sources=(('Bad','https://bad.ru/?q={q}','bad.ru'),('Good','https://good.ru/?q={q}','good.ru'))
  def fetch(url):
   if 'bad.ru' in url:raise TimeoutError()
   return '<a href="/item/RC-TWN28HN">RC-TWN28HN</a>',url,200
  x=discover_candidate_urls('Royal Clima RC-TWN28HN',fetcher=fetch,sources=sources,public_search_sources=())
  self.assertEqual(x['candidate_count'],1);self.assertEqual(x['source_reports'][0]['status'],'error')
 def test_captcha_not_parsed(self):
  x=discover_candidate_urls('RC-TWN28HN',fetcher=lambda u:('smart-captcha <a href="/RC-TWN28HN">x</a>',u,200),sources=(('X','https://x.ru/?q={q}','x.ru'),),public_search_sources=())
  self.assertEqual(x['candidate_count'],0);self.assertEqual(x['source_reports'][0]['status'],'captcha')

 def test_public_search_returns_direct_exact_candidate(self):
  rss=('<?xml version="1.0"?><rss><channel><item>'
       '<title>Royal Clima RC-TWN28HN — магазин</title>'
       '<link>https://shop.ru/product/rc-twn28hn</link>'
       '<description>Карточка RC-TWN28HN</description></item></channel></rss>')
  x=discover_candidate_urls('RC-TWN28HN',fetcher=lambda u:(rss,u,200),sources=(),
                            public_search_sources=(('Public','https://search/?q={q}'),))
  self.assertEqual(x['candidate_count'],1)
  self.assertEqual(x['candidate_urls'][0]['url'],'https://shop.ru/product/rc-twn28hn')

if __name__=='__main__':unittest.main()
