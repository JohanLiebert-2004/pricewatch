"""Export a public-only snapshot and publish it over the owner's existing SSH connection."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import threading
import uuid

_publisher_busy = threading.Lock()


def public_snapshot(payload):
    products = []
    catalogue = []
    checked = 0
    fields = ("asin", "title", "cents", "reference_cents", "advertised_discount_percent",
              "observed", "baseline_cents", "historical_drop_percent", "signals", "double_checked")
    for product in payload.get("products", []):
        if not re.fullmatch(r"[A-Z0-9]{10}", product.get("asin", "")):
            continue
        checked += bool(product.get("observations"))
        au = product.get("delivery_country") == "AU" and product.get("delivery_postcode") == "6000" and not product.get("excluded_from_ranking")
        priced = au and type(product.get("cents")) is int and product["cents"] > 0
        title = product.get("title")
        if not title or title == product["asin"]:
            title = next((lead.get("title") for lead in product.get("leads", []) if lead.get("title")), None) or "Amazon item " + product["asin"]
        entry = {name: product.get(name) if au else None for name in fields}
        entry.update(asin=product["asin"], title=title,
                     status="priced" if priced else "unavailable" if au and product.get("unavailable") else "price_unconfirmed" if au else "awaiting_check" if not product.get("observations") else "awaiting_au_check")
        if not priced:
            entry.update(cents=None, reference_cents=None, advertised_discount_percent=None,
                         baseline_cents=None, historical_drop_percent=None, signals=[], double_checked=False)
        elif type(product.get("reference_cents")) is int and product["reference_cents"] > product["cents"]:
            entry["advertised_discount_percent"] = round(100 * (product["reference_cents"] - product["cents"]) / product["reference_cents"], 1)
        catalogue.append(entry)
        if product.get("delivery_country") != "AU" or product.get("delivery_postcode") != "6000":
            continue
        if product.get("excluded_from_ranking") or type(product.get("cents")) is not int or product["cents"] <= 0:
            continue
        products.append({name: product.get(name) for name in fields})
    quote_fields = ("asin", "title", "quoted_cents", "reference_cents", "published", "expired", "conditional", "source_url")
    quotes = [{name: lead.get(name) for name in quote_fields} for lead in payload.get("community_leads", [])
              if re.fullmatch(r"[A-Z0-9]{10}", lead.get("asin", "")) and not lead.get("expired")
              and type(lead.get("quoted_cents")) is int and lead["quoted_cents"] > 0]
    return {"schema_version": 1, "generated_at": payload["generated_at"], "currency": "AUD",
            "freshness_minutes": 15, "products": products, "catalogue": catalogue, "community_leads": quotes,
            "coverage": {"tracked_products": len(catalogue),
                         "checked_products": checked,
                         "priced_products": len(products)}}


def write_json(path, payload):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def publish_snapshot(payload, data, background=False):
    """Publication is opt-in. Failures are recorded privately and never break price collection."""
    data = Path(data)
    public_file = data / "amazon-public.json"
    snapshot = public_snapshot(payload)
    write_json(public_file, snapshot)
    if background:
        if not _publisher_busy.acquire(blocking=False):
            return
        def run():
            try:
                _push_snapshot(snapshot, data)
            finally:
                _publisher_busy.release()
        threading.Thread(target=run, name="amazon-publication", daemon=True).start()
    else:
        _push_snapshot(snapshot, data)


def _push_snapshot(snapshot, data):
    settings_file = data / "publish-target.json"
    if not settings_file.exists():
        return
    status_file = data / "publication-status.json"
    previous = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {}
    if time.time() - previous.get("last_attempt_epoch", 0) < 60:
        return
    lock = data / "publication.lock"
    # A terminated coordinator must not leave publication locked forever.
    if lock.exists() and time.time() - lock.stat().st_mtime > 120:
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return
    with os.fdopen(fd, "w") as handle:
        json.dump({"pid": os.getpid(), "started": time.time()}, handle)
    status = {**previous, "last_attempt_epoch": time.time()}
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        host = settings["host"]
        directory = settings["remote_directory"]
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*@[a-zA-Z0-9.-]+", host):
            raise ValueError("Invalid publication SSH destination")
        if directory != "/var/lib/dealwatch-amazon/public":
            raise ValueError("Unexpected public snapshot directory")
        options = ["-i", str(Path(settings["key"]).expanduser()), "-o", "BatchMode=yes",
                   "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8"]
        encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        incoming = directory + "/.incoming-" + uuid.uuid4().hex + ".json"
        script = ("import hashlib,json,os,sys; raw=sys.stdin.buffer.read(); s=json.loads(raw); "
                  "assert s['schema_version']==1 and s['currency']=='AUD'; p=sys.argv[1]; "
                  "f=open(p,'xb'); f.write(raw); f.close(); os.chmod(p,0o644); "
                  "os.replace(p,sys.argv[2]); print(hashlib.sha256(raw).hexdigest())")
        remote = " ".join(shlex.quote(x) for x in ["python3", "-c", script, incoming, directory + "/amazon-deals.json"])
        result = subprocess.run(["ssh", *options, host, remote], input=encoded, capture_output=True, timeout=25)
        if result.returncode:
            raise RuntimeError("Public snapshot atomic update failed")
        lines = result.stdout.decode("utf-8", "replace").strip().splitlines()
        if not lines or lines[-1] != digest:
            raise RuntimeError("Public snapshot checksum did not match")
        status.update(last_success=datetime.now(timezone.utc).isoformat(),
                      snapshot_generated_at=snapshot["generated_at"], sha256=digest, error=None)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        status["error"] = type(exc).__name__ + ": " + str(exc)[:150]
    finally:
        write_json(status_file, status)
        lock.unlink(missing_ok=True)
