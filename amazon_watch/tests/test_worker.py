from amazon_watch import worker
from amazon_watch.watch import FetchError


def test_verification_challenge_stops_the_entire_host(monkeypatch):
    class Refused:
        def __init__(self,*args):
            self.blocked=set()
            self.requests=0
        def get(self,url):
            self.requests+=1
            self.blocked.add('www.amazon.com.au')
            raise FetchError('Verification challenge')
    monkeypatch.setattr(worker,'Fetcher',Refused)
    result=worker.execute({'worker':'test','job_id':'blocked','asins':['B000000001','B000000002']})
    assert result['requests']==1
    assert result['observations']==[]
    assert result['blocked_hosts']==['www.amazon.com.au']


def test_browser_error_allows_one_recovery_without_abandoning_later_products(monkeypatch):
    class Browser:
        def __init__(self,*args):
            self.blocked=set()
            self.requests=0
            self.browser=None
            self.closed=0
        def get(self,url):
            self.requests+=1
            if self.requests==1:raise RuntimeError('WebDriver failure')
            return 'product'
        def close(self):self.closed+=1
    monkeypatch.setattr(worker,'FirefoxFetcher',Browser)
    monkeypatch.setattr(worker,'parse_product',lambda html,asin:{'asin':asin,'cents':10000})
    result=worker.execute({'worker':'test','job_id':'recovery','browser':'firefox','asins':['B000000001','B000000002']})
    assert result['requests']==2
    assert [x['asin'] for x in result['observations']]==['B000000002']
    assert result['blocked_hosts']==[]
