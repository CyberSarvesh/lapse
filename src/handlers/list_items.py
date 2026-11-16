"""GET /items — List all non-deleted items for an email, sorted by expiry date."""

from __future__ import annotations

from typing import Any

from common.auth import authenticated_email
from common.db import list_user_items
from common.http import json_response

# What the page needs. Storage keys, the upload's S3 key and the workflow's
# execution ARN stay on the server.
PUBLIC_FIELDS = (
    "itemId", "name", "type", "provider", "expiryDate", "status", "leadDays",
    "suggestedDate", "expiredOn", "followUpCount", "createdAt", "updatedAt",
)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Return the signed-in user's items sorted by expiryDate."""
    email = authenticated_email(event)
    if not email:
        return json_response(401, {"error": "Sign in first"})

    items = [{k: item[k] for k in PUBLIC_FIELDS if k in item} for item in list_user_items(email)]
    return json_response(200, {"items": items})
