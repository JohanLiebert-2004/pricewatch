"""Tiny read-only JSON API for the deal feed (optional).

Run locally:  python api_deals.py   -> http://localhost:8000/deals.json
For free hosting you usually DON'T need this - the static site can read
Supabase directly via its REST endpoint. This exists for local preview and
for exporting a static deals.json you can commit and serve anywhere.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

import db


def fetch_deals(limit=200):
    conn = db.connect()
    rows = conn.execute(
        """SELECT d.price, d.reference_price, d.signal, d.score, d.status, d.detected_at,
                  p.title, p.retailer, p.url, p.is_marketplace
           FROM deals d JOIN products p ON p.id = d.product_id
           WHERE d.status != 'expired'
           ORDER BY d.score DESC, d.detected_at DESC LIMIT ?""",
        (limit,)).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        r["is_marketplace"] = bool(r["is_marketplace"])
        r["pct_off"] = round(float(r["score"]) * 100)
        r["error_tier"] = float(r["score"]) >= 0.80
        out.append(r)
    return out


def export_json(path="web/deals.json"):
    with open(path, "w") as f:
        json.dump(fetch_deals(), f, indent=2, default=str)
    print(f"wrote {path}")


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/deals.json"):
            body = json.dumps(fetch_deals(), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()


if __name__ == "__main__":
    import sys
    if "--export" in sys.argv:
        export_json()
    else:
        print("serving http://localhost:8000/deals.json  (Ctrl-C to stop)")
        HTTPServer(("", 8000), H).serve_forever()
