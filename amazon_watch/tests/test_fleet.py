import json
from pathlib import Path

from amazon_watch.fleet import allowed_job, import_result, queues, shard
from amazon_watch.watch import connect, iso, parse_community_listing


def test_source_cooldowns_do_not_pause_an_independent_host():
    from amazon_watch.watch import now
    from datetime import timedelta
    until = iso(now() + timedelta(minutes=30))
    job = {'sources':['https://www.ozbargain.com.au/deals/amazon.com.au', 'https://www.amazon.com.au/blackfriday'], 'asins':['B0DGWTGZQH']}
    filtered = allowed_job(job, {'paused_hosts':{'www.ozbargain.com.au':until}})
    assert filtered['asins']==job['asins']
    assert filtered['sources']==['https://www.amazon.com.au/blackfriday']
    filtered = allowed_job(job, {'paused_hosts':{'www.amazon.com.au':until}})
    assert filtered['asins']==[]
    assert filtered['sources']==['https://www.ozbargain.com.au/deals/amazon.com.au']


def test_listing_keeps_quotes_separate_and_handles_delivery_rrp():
    page = '''<div class="node node-ozbdeal"><h2 class="title" data-title="Camera $100 (RRP $400) Delivered @ Amazon AU"><a href="/node/123">Camera</a></h2>
      <a title="Go to https://www.amazon.com.au/dp/B0DGWTGZQH"></a><div class="submitted">on 17/11/2026 - 12:00</div><div class="content">Limited time deal</div></div>'''
    leads = parse_community_listing(page)
    assert len(leads) == 1
    assert leads[0]["quoted_cents"] == 10000
    assert leads[0]["reference_cents"] == 40000
    assert leads[0]["limited"] and not leads[0]["expired"]
    assert parse_community_listing(page.replace('node node-ozbdeal', 'node node-ozbdeal expired'))[0]["expired"]


def test_shards_do_not_overlap_and_result_import_is_idempotent(tmp_path):
    db = connect(tmp_path / "watch.sqlite3")
    nodes = [{"name": name} for name in ["Windows", "Linux", "Oracle1", "Oracle2"]]
    asins = [f"B{n:09d}" for n in range(100)]
    for asin in asins:
        db.execute("INSERT INTO products VALUES(?,?,?)", (asin, asin, iso()))
    db.commit()
    assignments = queues(db, nodes, 100)
    assigned = [asin for values in assignments.values() for asin in values]
    assert sorted(assigned) == asins
    assert len(assigned) == len(set(assigned))
    for i, node in enumerate(nodes):
        assert all(shard(asin, nodes) == i for asin in assignments[node["name"]])
    observed = iso()
    result = {"started": observed, "finished": observed, "discovered": [], "leads": [],
              "observations": [{"asin": asins[0], "title": "Camera", "observed": observed,
                                "cents": 10000, "seller_key": "seller", "worker": "Linux"}]}
    import_result(db, result)
    import_result(db, result)
    assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    db.close()


def test_out_of_order_workers_preserve_latest_price_and_delivery_matching(tmp_path, monkeypatch):
    import amazon_watch.watch as watch
    from datetime import timedelta
    monkeypatch.setattr(watch, "ROOT", tmp_path)
    monkeypatch.setattr(watch, "DATA", tmp_path / "data")
    config = json.loads((Path(__file__).parents[1] / 'config.json').read_text())
    db = connect(tmp_path / 'data/watch.sqlite3')
    at = watch.now()
    observations = [
        {'observed':iso(at), 'cents':10000, 'worker':'Linux', 'delivery_country':'AU'},
        {'observed':iso(at-timedelta(minutes=1)), 'cents':12000, 'worker':'Oracle', 'delivery_country':'AU'},
        {'observed':iso(at-timedelta(minutes=2)), 'cents':10000, 'worker':'Other', 'delivery_country':'IN'},
    ]
    for observation in observations:
        observation.update(asin='B0DGWTGZQH', title='Camera', seller_key='seller-a', delivery_postcode='6000')
        import_result(db, {'started':iso(at),'finished':iso(at),'discovered':[],'leads':[],'observations':[observation]})
    report = watch.generate_report(db, config)
    assert report['products'][0]['cents']==10000
    assert report['products'][0]['matching_workers']==['Linux']
    assert not report['products'][0]['double_checked']
    db.close()
