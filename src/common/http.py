"""HTTP response and request parsing helpers for API Gateway HTTP API v2."""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from typing import Any


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
}


def json_response(status_code: int, body: Any) -> dict[str, Any]:
    """Return an API Gateway v2 JSON response with CORS headers."""
    return {
        "statusCode": status_code,
        "headers": {**CORS_HEADERS, "Content-Type": "application/json"},
        "body": json.dumps(body, cls=_DecimalEncoder),
    }


def html_response(status_code: int, body: str) -> dict[str, Any]:
    """Return an API Gateway v2 HTML response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    """Decode the JSON object in a request body (handling base64 if present).

    A body that isn't a JSON object (malformed, a list, a bare string) reads as
    ``{}`` so handlers answer with their usual "missing field" 400 instead of a 500.
    """
    raw = event.get("body") or "{}"
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        body = json.loads(raw)
    except ValueError:  # includes bad base64, bad UTF-8 and bad JSON
        return {}
    return body if isinstance(body, dict) else {}


def clean_text(value: Any, max_length: int) -> str:
    """User-supplied text as a trimmed single-line string of at most ``max_length``."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return " ".join(str(value).split())[:max_length].strip()
