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
import uuid


def public_snapshot(payload):
    products = []
    fields = ("asin", "title", "cents", "reference_cents", "advertised_discount_percent",
              "observed", "baseline_cents", "historical_drop_percent", "signals", "double_checked")
    for product in payload.get("products", []):
        if not re.fullmatch(r"[A-Z0-9]{10}", product.get("asin", "")):
            continue
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
            "freshness_minutes": 15, "products": products, "community_leads": quotes}


def write_json(path, payload):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)


def publish_snapshot(payload, data):
    """Publication is opt-in. Failures are recorded privately and never break price collection."""
    data = Path(data)
    public_file = data / "amazon-public.json"
    write_json(public_file, public_snapshot(payload))
    settings_file = data / "publish-target.json"
    if not settings_file.exists():
        return
    status_file = data / "publication-status.json"
    previous = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {}
    if time.time() - previous.get("last_attempt_epoch", 0) < 60:
        return
    lock = data / "publication.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return
    os.close(fd)
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
        incoming = directory + "/.incoming-" + uuid.uuid4().hex + ".json"
        result = subprocess.run(["scp", *options, str(public_file), host + ":" + incoming],
                                capture_output=True, timeout=35)
        if result.returncode:
            raise RuntimeError("Public snapshot upload failed")
        # Parse before an atomic rename. Only the sanitized snapshot is uploaded.
        script = ("import json,os,sys; p=sys.argv[1]; d=sys.argv[2]; "
                  "s=json.load(open(p)); assert s['schema_version']==1 and s['currency']=='AUD'; "
                  "os.chmod(p,0o644); os.replace(p,d)")
        remote = " ".join(shlex.quote(x) for x in ["python3", "-c", script, incoming, directory + "/amazon-deals.json"])
        result = subprocess.run(["ssh", *options, host, remote], capture_output=True, timeout=25)
        if result.returncode:
            raise RuntimeError("Public snapshot atomic update failed")
        status.update(last_success=datetime.now(timezone.utc).isoformat(),
                      snapshot_generated_at=payload["generated_at"],
                      sha256=hashlib.sha256(public_file.read_bytes()).hexdigest(), error=None)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        status["error"] = type(exc).__name__ + ": " + str(exc)[:150]
    finally:
        write_json(status_file, status)
        lock.unlink(missing_ok=True)
