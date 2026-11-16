"""GET / and the app's static files — served over the API's HTTPS address (D29).

The files live in the private web bucket. Serving them from API Gateway gives
HTTPS without CloudFront (which new AWS accounts can't create until verified),
puts the page and the API on one origin, and lets the service worker for push
notifications (D31) live at the site root.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

from common.aws_clients import get_s3_client

CACHE_SECONDS = 30
_cache: dict[str, tuple[float, bytes]] = {}

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # The sign-in link carries a token in the URL: never send the path elsewhere.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=()",
}

# path -> (bucket key, content type, cache-control). Anything else is the app page.
FILES = {
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8", "no-cache"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json", "public, max-age=3600"),
    "/icon-192.png": ("icon-192.png", "image/png", "public, max-age=86400"),
    "/icon-512.png": ("icon-512.png", "image/png", "public, max-age=86400"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png", "public, max-age=86400"),
}
PAGE = ("index.html", "text/html; charset=utf-8", "no-cache")


def _read(key: str) -> bytes:
    now = time.time()
    hit = _cache.get(key)
    if hit is None or now - hit[0] > CACHE_SECONDS:
        obj = get_s3_client().get_object(Bucket=os.environ["WEB_BUCKET"], Key=key)
        hit = (now, obj["Body"].read())
        _cache[key] = hit
    return hit[1]


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    path = event.get("rawPath") or "/"
    key, content_type, cache = FILES.get(path, PAGE)
    try:
        data = _read(key)
        status = 200
    except Exception:
        data = b"<!doctype html><title>Lapse</title><p>Lapse is updating. Try again in a moment.</p>"
        key, content_type, cache, status = PAGE[0], PAGE[1], "no-cache", 503
    headers = {"Content-Type": content_type, "Cache-Control": cache, **SECURITY_HEADERS}
    if key == "sw.js":
        headers["Service-Worker-Allowed"] = "/"
    if content_type.startswith("image/"):
        return {"statusCode": status, "headers": headers, "body": base64.b64encode(data).decode(), "isBase64Encoded": True}
    return {"statusCode": status, "headers": headers, "body": data.decode("utf-8")}
