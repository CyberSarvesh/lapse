"""GET /items — List all non-deleted items for an email, sorted by expiry date."""

from __future__ import annotations

from typing import Any

from common.db import list_user_items
from common.http import json_response


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Return user's items sorted by expiryDate."""
    params = event.get("queryStringParameters") or {}
    email = (params.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return json_response(400, {"error": "Valid email query parameter is required"})

    items = list_user_items(email)
    return json_response(200, {"items": items})
