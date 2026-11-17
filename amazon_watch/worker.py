"""Small, finite sweep job. Credentials are handled by SSH, never by this script."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.error
import urllib.robotparser

if __package__:
    from .watch import FetchError, Fetcher, discover_asins, iso, parse_community_listing, parse_feed, parse_product
else:
    from watch import FetchError, Fetcher, discover_asins, iso, parse_community_listing, parse_feed, parse_product


class FirefoxFetcher(Fetcher):
    """Ordinary Firefox WebDriver, with no imported profile, stealth or CAPTCHA handling."""
    def __init__(self, delay=6):
        super().__init__(delay)
        self.browser = None
        self.http = Fetcher(delay)
        self.policy = None
        self.location_ready = False

    def set_location(self):
        """Use Amazon's normal delivery selector; no account or precise address is supplied."""
        browser = self.browser
        self.stage = "delivery dialog"
        browser._command("POST", "/execute/sync", {"script": "document.querySelector('#glow-ingress-block').click();return true;", "args": []})
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            ready = browser._command("POST", "/execute/sync", {"script": "const e=document.querySelector('#GLUXPostalCodeWithCity_PostalCodeInput');return !!(e&&e.getClientRects().length&&!e.disabled);", "args": []})
            if ready:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Australian delivery selector did not open")
        self.stage = "delivery postcode entry"
        browser._command("POST", "/execute/sync", {"script": "const e=document.querySelector('#GLUXPostalCodeWithCity_PostalCodeInput');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'6000');e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'0'}));e.dispatchEvent(new Event('change',{bubbles:true}));return e.value;", "args": []})
        self.stage = "delivery city options"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ready = browser._command("POST", "/execute/sync", {"script": "return [...document.querySelector('#GLUXPostalCodeWithCity_DropdownList').options].some(e=>e.value==='PERTH');", "args": []})
            if ready:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Australian postcode city was not offered")
        self.stage = "delivery city selection and apply"
        browser._command("POST", "/execute/sync", {"script": "const e=document.querySelector('#GLUXPostalCodeWithCity_DropdownList');e.value='PERTH';e.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#GLUXPostalCodeWithCityApplyButton input').click();return true;", "args": []})
        time.sleep(1)
        self.stage = "delivery confirmation"
        browser._command("POST", "/execute/sync", {"script": "const b=document.querySelector('#GLUXConfirmClose');if(b&&b.getClientRects().length)b.click();return true;", "args": []})
        self.location_ready = True

    def get(self, url):
        if urllib.parse.urlsplit(url).hostname != "www.amazon.com.au":
            return super().get(url)
        if "www.amazon.com.au" in self.blocked:
            raise FetchError("Amazon browser access paused after verification challenge")
        if self.policy is None:
            self.policy = urllib.robotparser.RobotFileParser()
            self.policy.parse(self.http.raw("https://www.amazon.com.au/robots.txt").splitlines())
        if not self.policy.can_fetch("BlackFridayWatch", url):
            raise FetchError("Source robots policy disallows this URL")
        if self.browser is None:
            if __package__:
                from .firefox_transport import FirefoxSession
            else:
                from firefox_transport import FirefoxSession
            self.browser = FirefoxSession("www.amazon.com.au")
        time.sleep(max(0, self.delay - (time.monotonic() - self.last.get("www.amazon.com.au", 0))))
        self.last["www.amazon.com.au"] = time.monotonic()
        self.requests += 1
        self.stage = "opening page"
        response = self.browser.get(url)
        lower = response.text.lower()
        if response.status_code in (403, 429, 503) or any(marker in lower for marker in (
            "validatecaptcha", "enter the characters you see below", "sorry, we just need to make sure you're not a robot")):
            self.blocked.add("www.amazon.com.au")
            raise FetchError("Firefox returned an Amazon verification challenge; browser sweep stopped")
        if response.status_code != 200:
            raise FetchError(f"Firefox navigation returned HTTP {response.status_code}")
        if not self.location_ready:
            self.set_location()
            self.stage = "confirming Australian delivery"
            response = self.browser.get(url)
            lower = response.text.lower()
            if "validatecaptcha" in lower or "enter the characters you see below" in lower:
                self.blocked.add("www.amazon.com.au")
                raise FetchError("Amazon verification challenge after delivery selection")
        from bs4 import BeautifulSoup
        location = BeautifulSoup(response.text, "html.parser").select_one("#glow-ingress-line2")
        if not location or not re.search(r'6000|Perth', location.get_text(" ", strip=True), re.I):
            raise RuntimeError("Australian delivery context not confirmed; no observation accepted")
        self.stage = "reading product price"
        return response.text

    def close(self):
        if self.browser:
            self.browser.close()


def execute(job, checkpoint=None):
    worker = job["worker"]
    started = iso()
    result = {"worker": worker, "job_id": job["job_id"], "started": started,
              "observations": [], "leads": [], "discovered": [], "errors": [], "blocked_hosts": []}
    fetcher = (FirefoxFetcher if job.get("browser") == "firefox" else Fetcher)(job.get("delay", 6))
    deadline = time.monotonic() + job.get("budget_seconds", 240)
    restarts = 0
    def recover():
        nonlocal restarts
        if isinstance(fetcher, FirefoxFetcher) and restarts < 1 and "www.amazon.com.au" not in fetcher.blocked:
            fetcher.close()
            fetcher.browser = None
            fetcher.location_ready = False
            restarts += 1
            return True
        return False
    for url in job.get("sources", []):
        if time.monotonic() >= deadline:
            break
        try:
            text = fetcher.get(url)
            if "ozbargain.com.au/deals/" in url:
                leads = parse_feed(text) if "/feed" in url else parse_community_listing(text)
                result["leads"].extend(leads)
                result["discovered"].extend(x["asin"] for x in leads if not x["expired"])
            else:
                result["discovered"].extend(discover_asins(text))
        except (FetchError, ET.ParseError, RuntimeError, TimeoutError, urllib.error.URLError) as exc:
            result["errors"].append({"source": url, "message": str(exc)})
            if not isinstance(exc, (FetchError, ET.ParseError)):
                recover()
        if checkpoint:
            checkpoint({**result, "finished": iso(), "progress": True})
    for asin in job.get("asins", []):
        if time.monotonic() >= deadline:
            break
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            continue
        if "www.amazon.com.au" in fetcher.blocked:
            break
        try:
            observation = parse_product(fetcher.get(f"https://www.amazon.com.au/dp/{asin}"), asin)
            observation.update(observed=iso(), worker=worker, job_id=job["job_id"], transport=job.get("browser", "http"))
            if job.get("browser") == "firefox":
                observation.update(delivery_country="AU", delivery_postcode="6000")
            result["observations"].append(observation)
            if checkpoint:
                checkpoint({**result, "finished": iso(), "progress": True})
        except (FetchError, RuntimeError, TimeoutError, urllib.error.URLError) as exc:
            result["errors"].append({"asin": asin, "message": str(exc) + (" at " + fetcher.stage if hasattr(fetcher, "stage") else "")})
            if checkpoint:
                checkpoint({**result, "finished": iso(), "progress": True})
            if not isinstance(exc, FetchError) and not recover():
                break
    if hasattr(fetcher, "close"):
        fetcher.close()
    result.update(finished=iso(), requests=fetcher.requests,
                  blocked_hosts=sorted(fetcher.blocked), discovered=list(dict.fromkeys(result["discovered"])))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("output")
    args = parser.parse_args()
    destination = Path(args.output)
    def checkpoint(result):
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(result), encoding="utf-8")
        temporary.replace(destination)
    result = execute(json.loads(Path(args.job).read_text(encoding="utf-8")), checkpoint)
    checkpoint(result)
    print(json.dumps({k: result[k] for k in ("worker", "job_id", "finished", "requests", "blocked_hosts")}))


if __name__ == "__main__":
    main()
