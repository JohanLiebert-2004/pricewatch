from datetime import timedelta
import json

import pytest

from amazon_watch.watch import ROOT, FetchError, assess, discover_asins, iso, money, now, parse_feed, parse_product


@pytest.fixture
def config():
    return json.loads((ROOT / "config.json").read_text())


def observation(at, cents=10000, seller="seller-a", **kwargs):
    return {"observed": iso(at), "cents": cents, "seller_key": seller,
            "condition": "unspecified", **kwargs}


def test_aud_money_and_ambiguous_foreign_prices():
    assert money("A$1,234.56") == 123456
    assert money("$95.00") == 9500
    assert money("US$49.99") is None
    assert money("NZ$20") is None
    assert money("£20") is None
    assert money("$0.00") is None


def test_discovery_deduplicates_encoded_asins():
    assert discover_asins('/dp/B0DGWTGZQH &quot;asin&quot;:&quot;B0DGWTGZQH&quot; /dp/INVALID') == ["B0DGWTGZQH"]


def test_product_does_not_take_recommendation_or_reference_price():
    html = '''<span class="a-price"><span class="a-offscreen">$1.00</span></span>
      <span id="productTitle">Camera</span><input id="ASIN" value="B0DGWTGZQH">
      <div id="corePriceDisplay_desktop_feature_div">
        <span class="a-price a-text-price"><span class="a-offscreen">$200.00</span></span>
        <span class="a-price"><span class="a-offscreen">$80.00</span></span></div>
      <div id="availability">In stock</div>'''
    product = parse_product(html, "B0DGWTGZQH")
    assert product["cents"] == 8000
    assert product["reference_cents"] == 20000
    with pytest.raises(FetchError):
        parse_product(html, "B000000000")
    product = parse_product(html.replace("In stock", "Currently unavailable"), "B0DGWTGZQH")
    assert product["cents"] is None and product["unavailable"]


def test_feed_does_not_guess_multi_product_price():
    xml = '''<rss xmlns:ozb="https://www.ozbargain.com.au"><channel><item>
      <title>Camera $100, Lens $50 @ Amazon AU</title><link>https://www.ozbargain.com.au/node/1</link>
      <ozb:meta url="https://www.amazon.com.au/dp/B0DGWTGZQH"/></item></channel></rss>'''
    leads = parse_feed(xml)
    assert len(leads) == 1
    assert leads[0]["quoted_cents"] is None


def test_big_drop_needs_real_same_seller_history(config):
    at = now()
    history = [observation(at - timedelta(hours=h)) for h in (48, 30, 25, 2)]
    current = observation(at, 2000)
    result = assess(current, history, [], config, at)
    assert result["score"] == 80
    assert result["historical_drop_percent"] == 80
    assert assess(observation(at, 2000, seller="seller-b"), history, [], config, at)["score"] == 0
    assert assess(current, history[-1:], [], config, at)["score"] == 0
    assert assess(current, history * 20, [], config, at)["baseline_cents"] == 10000


def test_repeated_polls_are_not_independent_history(config):
    at = now()
    history = [observation(at - timedelta(minutes=90 + n)) for n in range(20)]
    assert assess(observation(at, 2000), history, [], config, at)["score"] == 0


def test_rrp_is_not_a_pricing_error_and_stale_prices_cannot_alert(config):
    at = now()
    current = observation(at, 1000, reference_cents=10000)
    result = assess(current, [], [], config, at)
    assert result["score"] <= 20
    assert result["historical_drop_percent"] is None
    assert assess(current, [], [{"reported_error": True}], config, at + timedelta(hours=1))["score"] == 0


def test_short_lived_requires_prior_high_and_successful_end(config):
    at = now()
    history = [observation(at - timedelta(hours=2), 10000),
               observation(at - timedelta(hours=1), 2000),
               observation(at - timedelta(minutes=5), 2000)]
    assert assess(observation(at, 10000), history, [], config, at)["short_lived_observed"]
    assert not assess(observation(at, None), history, [], config, at)["short_lived_observed"]
    assert not assess(observation(at, 10000), history[1:], [], config, at)["short_lived_observed"]
    history.insert(0, observation(at - timedelta(days=2), 10000))
    history[1] = observation(at - timedelta(days=1), 2000)
    assert not assess(observation(at, 10000), history, [], config, at)["short_lived_observed"]


def test_expired_leads_cannot_raise_priority(config):
    at = now()
    lead = {"reported_error": True, "limited": True, "expiry": iso(at - timedelta(seconds=1))}
    assert assess(observation(at), [], [lead], config, at)["score"] == 0


def test_community_priority_requires_matching_unconditional_price(config):
    at = now()
    lead = {"reported_error": True, "quoted_cents": 10000}
    assert assess(observation(at), [], [lead], config, at)["score"] == 35
    assert assess(observation(at, 20000), [], [lead], config, at)["score"] == 0
    assert assess(observation(at), [], [{**lead, "conditional": True}], config, at)["score"] == 0


def test_history_does_not_mix_delivery_countries(config):
    at = now()
    history = [observation(at - timedelta(hours=h), delivery_country="IN") for h in (48, 30, 25)]
    current = observation(at, 2000, delivery_country="AU", delivery_postcode="6000")
    assert assess(current, history, [], config, at)["baseline_cents"] is None


def test_report_pipeline_persists_events_without_repeated_alerts(config, tmp_path, monkeypatch):
    import amazon_watch.watch as watch
    monkeypatch.setattr(watch, "ROOT", tmp_path)
    monkeypatch.setattr(watch, "DATA", tmp_path / "data")
    db = watch.connect(tmp_path / "data" / "test.sqlite3")
    at = now()
    db.execute("INSERT INTO products VALUES(?,?,?)", ("B0DGWTGZQH", "Test camera", iso(at)))
    for hours, cents in [(48, 10000), (30, 10000), (25, 10000), (0, 2000)]:
        obs = observation(at - timedelta(hours=hours), cents, title="Test camera")
        db.execute("INSERT INTO observations(asin,observed,cents,seller_key,payload) VALUES(?,?,?,?,?)",
                   ("B0DGWTGZQH", obs["observed"], cents, "seller-a", json.dumps(obs)))
    db.commit()
    report = watch.generate_report(db, config)
    assert report["products"][0]["score"] == 80
    assert len(report["events"]) == 1
    assert len(watch.generate_report(db, config)["events"]) == 1
    assert (tmp_path / "report.html").is_file()
    assert json.loads((tmp_path / "data" / "latest.json").read_text())["events"][0]["cents"] == 2000
    db.close()
