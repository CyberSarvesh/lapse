"""Run the Lapse frontend against an in-memory fake API (no AWS, Docker or LocalStack).

Usage:
    python scripts/dev_mock.py [--port 8765] [--empty]

Serves web/ at http://127.0.0.1:8765 with the API URL baked in, and answers the
same routes the real API does with sample data that lives only in this process.
Sign in with any email and press "Local mode: open sign-in link". Uploads cycle
through the three outcomes the real reader has: date found, date suggested for
confirmation, and date not found.

For UI work only: nothing here exercises the Lambda handlers. Use the unit
tests and LocalStack (see README) for those.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB = Path(__file__).resolve().parents[1] / "web"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".webmanifest": "application/manifest+json",
    ".png": "image/png",
}
SESSION = "dev-session"
READ_SECONDS = 4

LOCK = threading.Lock()
ITEMS: dict[str, dict] = {}
CALENDAR: dict[str, object] = {}
upload_count = 0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _add(name: str, kind: str, provider: str, days: int | None, status: str = "ACTIVE", **extra: object) -> dict:
    item_id = uuid.uuid4().hex[:12]
    ITEMS[item_id] = {
        "itemId": item_id,
        "name": name,
        "type": kind,
        "provider": provider,
        "expiryDate": "" if days is None else _in(days),
        "status": status,
        "leadDays": 30,
        "createdAt": _now(),
        "updatedAt": _now(),
        **extra,
    }
    return ITEMS[item_id]


def seed() -> None:
    _add("Car insurance", "insurance", "Allstate", 5, leadDays=14)
    _add("Netflix annual plan", "subscription", "Netflix", 19, leadDays=7)
    _add("Passport", "passport", "", 74, leadDays=90)
    _add("Dishwasher warranty", "warranty", "Bosch", 131)
    _add("Home insurance", "insurance", "Lemonade", 139, leadDays=60)
    _add("Domain: example.dev", "subscription", "Namecheap", 250)
    _add("Driving licence", "passport", "DVLA", 900, leadDays=90)
    _add("Gym membership", "subscription", "PureGym", -6)
    _add("Boiler service plan", "other", "British Gas", None, status="NEEDS_DATE", suggestedDate=_in(210))
    _add("Laptop warranty", "warranty", "Lenovo", 320, status="RENEWED")
    _add("Old phone contract", "subscription", "", 12, status="IGNORED")


def _finish_upload(item_id: str, outcome: int) -> None:
    time.sleep(READ_SECONDS)
    with LOCK:
        item = ITEMS.get(item_id)
        if not item or item["status"] != "PROCESSING":
            return
        if outcome == 0:
            item.update(name="Travel insurance", type="insurance", provider="World Nomads", expiryDate=_in(200), status="ACTIVE")
        elif outcome == 1:
            item.update(name="Appliance receipt", type="warranty", status="NEEDS_DATE", suggestedDate=_in(365))
        else:
            item.update(name="Uploaded Document", status="NEEDS_DATE")
        item["updatedAt"] = _now()


class Handler(BaseHTTPRequestHandler):
    server_version = "LapseMock/1"

    def log_message(self, fmt: str, *args: object) -> None:  # quieter console
        if not self.path.startswith("/items"):
            super().log_message(fmt, *args)

    # ---- helpers ----
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, body: object) -> None:
        self._send(status, json.dumps(body).encode(), "application/json")

    def _body(self) -> dict:
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _signed_in(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {SESSION}":
            return True
        self._json(401, {"error": "Sign in first"})
        return False

    def _origin(self) -> str:
        return f"http://{self.headers.get('Host')}"

    def _static(self, path: str) -> None:
        name = path.lstrip("/") or "index.html"
        file = (WEB / name).resolve()
        if WEB not in file.parents or not file.is_file():
            file = WEB / "index.html"
        data = file.read_bytes()
        if file.name == "index.html":
            data = data.replace(b"__LAPSE_API_URL__", self._origin().encode())
        self._send(200, data, CONTENT_TYPES.get(file.suffix, "application/octet-stream"))

    # ---- routes ----
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path == "/items":
            if self._signed_in():
                with LOCK:
                    items = sorted(ITEMS.values(), key=lambda i: (i["expiryDate"] or "9999-12-31", i["createdAt"]))
                    self._json(200, {"items": items})
        elif url.path == "/upload-url":
            if self._signed_in():
                ext = (parse_qs(url.query).get("ext") or ["jpg"])[0]
                with LOCK:
                    item = _add("Reading document…", "other", "", None, status="PROCESSING")
                self._json(200, {"uploadUrl": f"{self._origin()}/mock-upload/{item['itemId']}", "itemId": item["itemId"], "s3Key": f"uploads/{item['itemId']}.{ext}"})
        elif url.path == "/calendar-feed":
            if self._signed_in():
                self._json(200, CALENDAR)
        else:
            self._static(url.path)

    def do_PUT(self) -> None:
        global upload_count
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        item_id = self.path.rsplit("/", 1)[-1]
        with LOCK:
            outcome = upload_count % 3
            upload_count += 1
        threading.Thread(target=_finish_upload, args=(item_id, outcome), daemon=True).start()
        self._send(200, b"", "text/plain")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._body()
        if path == "/auth/start":
            self._json(200, {"sent": True, "devLoginUrl": f"{self._origin()}/?login=dev"})
        elif path == "/auth/verify":
            self._json(200, {"token": SESSION, "email": "you@example.com"})
        elif path == "/auth/demo":
            self._json(200, {"token": SESSION, "email": "guest-mock@demo.lapse.invalid", "demo": True})
        elif not self._signed_in():
            return
        elif path == "/auth/logout":
            self._json(200, {"ok": True})
        elif path == "/items":
            expiry = str(body.get("expiryDate") or "")
            try:
                if date.fromisoformat(expiry) < date.today() - timedelta(days=1):
                    return self._json(400, {"error": "That date has already passed. Enter the date the new one expires."})
            except ValueError:
                return self._json(400, {"error": "expiryDate must be a real date in YYYY-MM-DD format"})
            with LOCK:
                item = ITEMS.get(str(body.get("itemId") or "")) or _add("Untitled Item", "other", "", None)
                for key in ("name", "type", "provider"):
                    if body.get(key):
                        item[key] = str(body[key]).strip()
                item.update(expiryDate=expiry, status="ACTIVE", leadDays=int(body.get("leadDays") or item.get("leadDays") or 30), updatedAt=_now())
                item.pop("suggestedDate", None)
                item.pop("expiredOn", None)
                self._json(200, {"item": item})
        elif path == "/calendar-feed":
            if body.get("rotate") or not CALENDAR:
                CALENDAR.clear()
                CALENDAR["url"] = f"{self._origin()}/calendar/{uuid.uuid4().hex}.ics"
            self._json(200, CALENDAR)
        elif path.startswith("/push/"):
            self._json(200, {"ok": True, "sent": 0})
        else:
            self._json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:
        if not self._signed_in():
            return
        with LOCK:
            removed = ITEMS.pop(self.path.rsplit("/", 1)[-1], None)
        self._json(200 if removed else 404, {"deleted": True} if removed else {"error": "Item not found"})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--empty", action="store_true", help="start with no items (to see the first-run screens)")
    args = parser.parse_args()
    if not args.empty:
        seed()
    print(f"Lapse with a fake API: http://127.0.0.1:{args.port} (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
