"""Coordinate disjoint, bounded sweep jobs across the owner's existing computers."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid
import urllib.parse

if __package__:
    from .watch import ROOT, DATA, connect, date, generate_report, iso, now
    from .worker import execute
else:
    from watch import ROOT, DATA, connect, date, generate_report, iso, now
    from worker import execute

REMOTE = ".local/share/blackfriday-watch"


def save_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


def command(args, timeout=90):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        # Only this project's SSH/scp commands are executed; no environment contents are read.
        raise RuntimeError(result.stderr.strip()[-600:] or f"Command exited {result.returncode}")
    return result.stdout


def ssh_options(node):
    return ["-i", str(Path(node["key"]).expanduser()), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]


def provision(node):
    if node.get("local"):
        return
    # Idempotent source upload to our isolated directory; no existing service is modified.
    command(["scp", *ssh_options(node), str(ROOT / "watch.py"), str(ROOT / "worker.py"), str(ROOT / "firefox_transport.py"), f'{node["host"]}:{REMOTE}/'], timeout=30)


def remote_job(node, job):
    if node.get("local"):
        return execute(job)
    job_path = DATA / "jobs" / f'{job["job_id"]}.json'
    result_path = DATA / "jobs" / f'{job["job_id"]}-result.json'
    remote_input = "job-" + job["job_id"] + ".json"
    remote_output = "result-" + job["job_id"] + ".json"
    save_json(job_path, job)
    command(["scp", *ssh_options(node), str(job_path), f'{node["host"]}:{REMOTE}/{remote_input}'], timeout=60)
    seconds = 300 if job["asins"] else 75
    remote_command = f"cd ~/{REMOTE} && timeout --signal=TERM --kill-after=5s {seconds}s nice -n 10 venv/bin/python worker.py {remote_input} {remote_output}"
    if job.get("asins") and job.get("browser") == "firefox" and node.get("bounded_browser"):
        base = "/home/ubuntu/" + REMOTE
        environment = [f"{key}={value}" for key, value in node.get("firefox_env", {}).items()]
        arguments = ["systemd-run", "--user", "--wait", "--pipe", "--collect", "--unit=bfw-" + job["job_id"][:12],
                     "-p", f"MemoryMax={int(node.get('memory_max_mb', 420))}M", "-p", f"MemorySwapMax={int(node.get('memory_swap_mb', 256))}M", "-p", "CPUQuota=50%", "-p", "RuntimeMaxSec=300",
                     "/usr/bin/env", *environment, "/usr/bin/nice", "-n", "10", base + "/venv/bin/python",
                     base + "/worker.py", base + "/" + remote_input, base + "/" + remote_output]
        remote_command = " ".join(shlex.quote(x) for x in arguments)
    command(["ssh", *ssh_options(node), node["host"], remote_command], timeout=420 if job["asins"] else 90)
    command(["scp", *ssh_options(node), f'{node["host"]}:{REMOTE}/{remote_output}', str(result_path)], timeout=60)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("job_id") != job["job_id"] or result.get("worker") != node["name"]:
        raise RuntimeError("Remote result identity mismatch")
    return result


def import_result(db, result):
    for asin in result["discovered"]:
        if re.fullmatch(r"[A-Z0-9]{10}", asin):
            db.execute("INSERT OR IGNORE INTO products VALUES(?,?,?)", (asin, asin, result["started"]))
    for lead in result["leads"]:
        db.execute("INSERT OR IGNORE INTO products VALUES(?,?,?)", (lead["asin"], lead["title"], result["started"]))
        db.execute("INSERT OR REPLACE INTO leads VALUES(?,?,?,?)", (lead["asin"], lead["source_url"], json.dumps(lead), result["finished"]))
    for observation in result["observations"]:
        asin = observation["asin"]
        job_id = observation.get("job_id") or result.get("job_id") or "import-" + observation["observed"]
        db.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?)",
                   (job_id, asin, observation["observed"], "ok", None, None))
        duplicate = db.execute("SELECT 1 FROM observations WHERE asin=? AND observed=?", (asin, observation["observed"])).fetchone()
        if duplicate:
            continue
        db.execute("INSERT OR IGNORE INTO products VALUES(?,?,?)", (asin, observation["title"], result["started"]))
        db.execute("UPDATE products SET title=? WHERE asin=?", (observation["title"], asin))
        db.execute("INSERT INTO observations(asin,observed,cents,seller_key,payload) VALUES(?,?,?,?,?)",
                   (asin, observation["observed"], observation["cents"], observation["seller_key"], json.dumps(observation)))
    for failure in result.get("errors", []):
        if not failure.get("asin") or not result.get("job_id"):
            continue
        attempted = date(result["finished"]) or now()
        hours = 6 if "identity not confirmed" in failure.get("message", "").lower() else 0.5
        db.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,?,?)",
                   (result["job_id"], failure["asin"], iso(attempted), "failed",
                    iso(attempted + timedelta(hours=hours)), failure.get("message", "")))
    db.commit()


def shard(asin, nodes):
    return int(hashlib.sha256(asin.encode()).hexdigest(), 16) % len(nodes)


def queues(db, nodes, size):
    products = [dict(r) for r in db.execute("SELECT p.asin,MAX(o.observed) AS checked FROM products p LEFT JOIN observations o ON p.asin=o.asin GROUP BY p.asin")]
    attempts = {}
    for row in db.execute("SELECT asin,attempted,retry_after FROM attempts ORDER BY attempted"):
        attempts[row["asin"]] = dict(row)
    products = [x for x in products if not (date(attempts.get(x["asin"], {}).get("retry_after"))
                and date(attempts[x["asin"]]["retry_after"]) > now()
                and (not x["checked"] or attempts[x["asin"]]["attempted"] >= x["checked"]))]
    for product in products:
        product["checked"] = max(attempts.get(product["asin"], {}).get("attempted") or "", product["checked"] or "")
    urgent = set()
    expired_status = {}
    for row in db.execute("SELECT asin,payload FROM leads"):
        lead = json.loads(row["payload"])
        expired_status.setdefault(row["asin"], []).append(bool(lead.get("expired")))
        deep_quote = lead.get("reference_cents") and lead.get("quoted_cents") and lead["quoted_cents"] <= lead["reference_cents"] * 0.5
        if not lead.get("expired") and (lead.get("reported_error") or lead.get("limited") or deep_quote):
            urgent.add(row["asin"])
    products = [x for x in products if not (x["asin"] in expired_status and all(expired_status[x["asin"]]))]
    result = {node["name"]: [] for node in nodes}
    eligible = [node for node in nodes if node.get("can_verify", True)]
    for i, node in enumerate(eligible):
        limit = node.get("batch_size", size)
        owned = [x for x in products if shard(x["asin"], eligible) == i]
        priority = sorted([x for x in owned if x["asin"] in urgent], key=lambda x: x["checked"] or "")[:limit // 2]
        chosen = {x["asin"] for x in priority}
        other = sorted([x for x in owned if x["asin"] not in chosen], key=lambda x: (x["checked"] or "", x["asin"]))
        result[node["name"]] = [x["asin"] for x in (priority + other)[:limit]]
    return result


def allowed_job(job, state):
    paused = {host for host, until in state.get("paused_hosts", {}).items() if date(until) and date(until) > now()}
    return {**job,
            "sources": [url for url in job["sources"] if urllib.parse.urlsplit(url).hostname not in paused],
            "asins": [] if "www.amazon.com.au" in paused else job["asins"]}


def run_batch(db, config, nodes, jobs, status):
    with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
        pending = {}
        for node in nodes:
            job = jobs.get(node["name"])
            if not job:
                continue
            if not job["sources"] and not job["asins"]:
                continue
            state = status["workers"].setdefault(node["name"], {})
            job = allowed_job(job, state)
            if not job["sources"] and not job["asins"]:
                state["state"] = "Source access paused"
                continue
            state.update(state="Discovering" if job["sources"] else "Checking prices", assigned=len(job["asins"]), started=job["job_id"])
            pending[pool.submit(remote_job, node, job)] = node
        save_json(DATA / "fleet_status.json", status)
        generate_report(db, config)
        remaining = set(pending)
        while remaining:
            completed, remaining = wait(remaining, timeout=20, return_when=FIRST_COMPLETED)
            if not completed:
                for future in remaining:
                    node = pending[future]
                    if node.get("local"):
                        continue
                    job = jobs[node["name"]]
                    if not job["asins"]:
                        continue
                    progress_path = DATA / "jobs" / (job["job_id"] + "-progress.json")
                    try:
                        command(["scp", *ssh_options(node), node["host"] + ":" + REMOTE + "/result-" + job["job_id"] + ".json", str(progress_path)], timeout=12)
                        partial = json.loads(progress_path.read_text(encoding="utf-8"))
                        if partial.get("job_id") == job["job_id"]:
                            import_result(db, partial)
                            status["workers"][node["name"]].update(checked=len(partial["observations"]), last_progress=iso(),
                                                                  state="Checking prices" if partial["observations"] else "Discovering")
                    except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                        pass
                status["updated"] = iso()
                save_json(DATA / "fleet_status.json", status)
                generate_report(db, config)
                continue
            for future in completed:
                finish_result(future, pending[future], db, config, status)


def finish_result(future, node, db, config, status):
    state = status["workers"][node["name"]]
    try:
        result = future.result()
        import_result(db, result)
        state.update(state="Completed with errors" if result["errors"] else "Completed", last_finished=result["finished"],
                     checked=len(result["observations"]), discovered=len(result["discovered"]),
                     errors=result["errors"], blocked_hosts=result["blocked_hosts"],
                     total_checked=state.get("total_checked", 0) + len(result["observations"]))
        state.pop("error", None)
        paused = state.setdefault("paused_hosts", {})
        for host in result["blocked_hosts"]:
            paused[host] = iso(now() + timedelta(minutes=30))
        paused = {host: until for host, until in paused.items() if date(until) and date(until) > now()}
        state["paused_hosts"] = paused
        if "www.amazon.com.au" in paused:
            state["state"] = "Amazon access paused"
        elif "www.ozbargain.com.au" in paused and not result["observations"]:
            state["state"] = "Community source paused"
        print(json.dumps({"worker": node["name"], "checked": len(result["observations"]), "discovered": len(result["discovered"]), "errors": result["errors"]}), flush=True)
    except Exception as exc:
        state.update(state="Connection/job failed", error=str(exc), last_finished=iso())
        print(f'{node["name"]}: {exc}', flush=True)
    status["updated"] = iso()
    save_json(DATA / "fleet_status.json", status)
    generate_report(db, config)


def cycle(db, config, nodes, status, discover=True):
    started = iso()
    status["phase"] = "Discovery and price checks" if discover else "Price checks"
    assignments = queues(db, nodes, config.get("fleet_batch_size", 16))
    jobs = {node["name"]: {"job_id": uuid.uuid4().hex, "worker": node["name"], "delay": node["delay"], "browser": node.get("browser", "http"),
                           "sources": list(node.get("sources", [])) if discover else [], "asins": assignments[node["name"]],
                           "budget_seconds": config.get("fleet_job_budget_seconds", 240)} for node in nodes}
    if discover:
        eligible_discovery = [node for node in nodes if node.get("can_verify", True)]
        for i, url in enumerate(config.get("amazon_discovery_sources", [])):
            if eligible_discovery:
                jobs[eligible_discovery[i % len(eligible_discovery)]["name"]]["sources"].append(url)
        for job in jobs.values():
            job["sources"] = list(dict.fromkeys(job["sources"]))
    # Community-only jobs use HTTP, with a shorter deadline, and cannot hold up the price worker.
    for job in jobs.values():
        if not job["asins"] and not any(urllib.parse.urlsplit(url).hostname == "www.amazon.com.au" for url in job["sources"]):
            job["browser"] = "http"
    before = db.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
    run_batch(db, config, nodes, jobs, status)
    # Second sweep: corroborate successful top offers on a different predetermined worker.
    # A denied or failed lookup is never retried through a different machine.
    report = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    second = {node["name"]: [] for node in nodes}
    eligible = [node for node in nodes if node.get("can_verify", True)]
    for product in [x for x in report["products"] if x.get("cents") and x.get("fresh")
                    and (x.get("score", 0) >= 35 or x.get("advertised_discount_percent", 0) >= 50)][:12]:
        original = product.get("worker", "Windows laptop")
        original_i = next((i for i, node in enumerate(eligible) if node["name"] == original), shard(product["asin"], eligible))
        target = eligible[(original_i + 1) % len(eligible)]
        if target["name"] == original or len(eligible) < 2:
            continue
        target_state = status["workers"].get(target["name"], {})
        if target_state.get("state") not in {"Connection/job failed", "Source access paused"}:
            second[target["name"]].append(product["asin"])
    second_jobs = {node["name"]: {"job_id": uuid.uuid4().hex, "worker": node["name"], "delay": node["delay"], "browser": node.get("browser", "http"),
                                  "sources": [], "asins": second[node["name"]]} for node in nodes if second[node["name"]]}
    if second_jobs:
        status["phase"] = "Second sweep of leading offers"
        run_batch(db, config, nodes, second_jobs, status)
    checked = db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] - before
    job_ids = [x["job_id"] for x in [*jobs.values(), *second_jobs.values()]]
    attempted = db.execute("SELECT COUNT(*) FROM attempts WHERE job_id IN (" + ",".join("?" for _ in job_ids) + ")", job_ids).fetchone()[0]
    summary = {"started": started, "finished": iso(), "checked": checked,
               "assigned": sum(len(x["asins"]) for x in jobs.values()), "attempted": attempted, "fleet": True,
               "errors": [f'{name}: {s.get("error", s["state"])}' for name, s in status["workers"].items()
                          if s["state"] in {"Connection/job failed", "Source access paused"}]}
    db.execute("INSERT INTO runs(started,finished,payload) VALUES(?,?,?)", (started, summary["finished"], json.dumps(summary)))
    db.commit()
    status.update(updated=iso(), cycle_finished=iso(), phase="Waiting for next sweep")
    save_json(DATA / "fleet_status.json", status)
    generate_report(db, config)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["once", "watch"])
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    nodes = json.loads((DATA / "fleet-hosts.json").read_text())["nodes"]
    (DATA / "jobs").mkdir(parents=True, exist_ok=True)
    lock = DATA / "fleet.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit("Fleet is already running, or fleet.lock is stale")
    with os.fdopen(fd, "w") as handle:
        handle.write(str(os.getpid()))
    prior = DATA / "fleet_status.json"
    status = json.loads(prior.read_text()) if prior.exists() else {"workers": {}}
    status.update(coordinator="Windows laptop", updated=iso(), interval_seconds=config["scan_interval_seconds"])
    try:
        with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
            pending = {pool.submit(provision, node): node for node in nodes}
            for future in as_completed(pending):
                node = pending[future]
                try:
                    future.result()
                    print(f'{node["name"]}: source ready', flush=True)
                except Exception as exc:
                    status["workers"][node["name"]] = {"state": "Connection/job failed", "error": str(exc)}
        db = connect()
        count = 0
        while True:
            began = time.monotonic()
            try:
                cycle(db, config, nodes, status, discover=count % 3 == 0)
            except Exception as exc:
                print(f"Cycle failed: {type(exc).__name__}: {exc}", flush=True)
            if args.command == "once":
                break
            count += 1
            time.sleep(max(30, config["scan_interval_seconds"] - (time.monotonic() - began)))
        db.close()
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
