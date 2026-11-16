"""GET / — serve the web app over the API's HTTPS address (D29).

The page lives in the private web bucket. Serving it from API Gateway gives it
HTTPS without CloudFront (which new AWS accounts can't create until verified)
and puts the page and the API on one origin.
"""

from __future__ import annotations

import os
import time
from typing import Any

from common.aws_clients import get_s3_client

CACHE_SECONDS = 30
_cache: dict[str, Any] = {"html": None, "at": 0.0}

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # The sign-in link carries a token in the URL: never send the path elsewhere.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=()",
}


def _page() -> str:
    now = time.time()
    if _cache["html"] is None or now - _cache["at"] > CACHE_SECONDS:
        obj = get_s3_client().get_object(Bucket=os.environ["WEB_BUCKET"], Key="index.html")
        _cache["html"] = obj["Body"].read().decode("utf-8")
        _cache["at"] = now
    return _cache["html"]


def handler(_event: dict[str, Any], _context: Any) -> dict[str, Any]:
    try:
        body = _page()
        status = 200
    except Exception:
        body = "<!doctype html><title>Lapse</title><p>Lapse is updating. Try again in a moment.</p>"
        status = 503
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-cache",
            **SECURITY_HEADERS,
        },
        "body": body,
    }
