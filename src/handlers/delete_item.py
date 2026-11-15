"""DELETE /items/{itemId} — Soft-delete an item to stop future reminders (D12)."""

from __future__ import annotations

from typing import Any

from common.auth import authenticated_email
from common.db import STATUS_DELETED, get_item, put_item, utc_now_iso
from common.http import json_response


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Mark an item as DELETED so the waiting workflow exits cleanly after its Wait."""
    email = authenticated_email(event)
    if not email:
        return json_response(401, {"error": "Sign in first"})
    item_id = ((event.get("pathParameters") or {}).get("itemId") or "").strip()
    if not item_id:
        return json_response(400, {"error": "itemId path parameter is required"})

    existing = get_item(email, item_id)
    if not existing:
        return json_response(404, {"error": "Item not found"})

    existing["status"] = STATUS_DELETED
    existing["updatedAt"] = utc_now_iso()
    put_item(existing)

    return json_response(200, {"deleted": True, "itemId": item_id})
