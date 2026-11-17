"""Read-only local dashboard. Serve only the report and public deal data."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
import os
DATA = Path(os.environ.get("AMAZON_WATCH_DATA_DIR", str(ROOT / "data")))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {"/": ("dashboard.html", "text/html; charset=utf-8"),
                  "/api/latest": (DATA / "latest.json", "application/json; charset=utf-8"),
                  "/api/fleet": (DATA / "fleet_status.json", "application/json; charset=utf-8")}
        route = routes.get(urlsplit(self.path).path)
        if not route:
            self.send_error(404)
            return
        path, content_type = route
        file = ROOT / path
        if not file.exists():
            self.send_error(503, "First sweep is still collecting results")
            return
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def main():
    print("Black Friday Watch: http://127.0.0.1:8765", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()


if __name__ == "__main__":
    main()
