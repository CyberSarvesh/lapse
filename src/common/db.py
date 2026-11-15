"""Single-table DynamoDB key builders and item helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Key

from common.aws_clients import get_dynamodb_table

STATUS_PROCESSING = "PROCESSING"
STATUS_NEEDS_DATE = "NEEDS_DATE"
STATUS_ACTIVE = "ACTIVE"
STATUS_RENEWED = "RENEWED"
STATUS_IGNORED = "IGNORED"
STATUS_DELETED = "DELETED"


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def user_pk(email: str) -> str:
    return f"USER#{email.strip().lower()}"


def item_sk(item_id: str) -> str:
    return f"ITEM#{item_id}"


def upload_pk(item_id: str) -> str:
    return f"UPLOAD#{item_id}"


def action_pk(action_id: str) -> str:
    return f"ACTION#{action_id}"


def get_item(email: str, item_id: str) -> dict[str, Any] | None:
    """Fetch a single item record for a user, or None if not found."""
    resp = get_dynamodb_table().get_item(
        Key={"PK": user_pk(email), "SK": item_sk(item_id)}
    )
    return resp.get("Item")


def put_item(item: dict[str, Any]) -> None:
    """Write a full item record."""
    get_dynamodb_table().put_item(Item=item)


STALE_PROCESSING_SECONDS = 15 * 60


def _surface_stale_processing(item: dict[str, Any]) -> dict[str, Any]:
    """Show an item stuck in PROCESSING (upload never finished, or extraction
    crashed) as NEEDS_DATE so the user can type the date instead of waiting forever."""
    if item.get("status") != STATUS_PROCESSING:
        return item
    try:
        created = datetime.strptime(item.get("createdAt", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return item
    if (datetime.now(timezone.utc) - created).total_seconds() > STALE_PROCESSING_SECONDS:
        name = item.get("name") or ""
        if not name or name.startswith("Reading document"):
            name = "Uploaded Document"
        return {**item, "status": STATUS_NEEDS_DATE, "name": name}
    return item


def list_user_items(email: str) -> list[dict[str, Any]]:
    """Return all non-deleted items for a user, sorted by expiryDate ascending."""
    resp = get_dynamodb_table().query(
        KeyConditionExpression=Key("PK").eq(user_pk(email))
        & Key("SK").begins_with("ITEM#")
    )
    items = [
        _surface_stale_processing(i)
        for i in resp.get("Items", [])
        if i.get("status") != STATUS_DELETED
    ]
    items.sort(key=lambda x: (x.get("expiryDate") or "9999-12-31", x.get("createdAt") or ""))
    return items


def put_upload_lookup(item_id: str, email: str, s3_key: str, ttl_seconds: int = 86400) -> None:
    """Store a temporary mapping from uploaded itemId to user email (default 1 day TTL)."""
    get_dynamodb_table().put_item(
        Item={
            "PK": upload_pk(item_id),
            "SK": "META",
            "itemId": item_id,
            "email": email.strip().lower(),
            "s3Key": s3_key,
            "ttl": int(time.time()) + ttl_seconds,
        }
    )


def get_upload_lookup(item_id: str) -> dict[str, Any] | None:
    """Retrieve upload lookup metadata by itemId."""
    resp = get_dynamodb_table().get_item(
        Key={"PK": upload_pk(item_id), "SK": "META"}
    )
    return resp.get("Item")


def put_action_link(
    action_id: str,
    task_token: str,
    email: str,
    item_id: str,
    action: str,
    item_name: str,
    expiry_date: str,
    sibling_id: str = "",
    ttl_seconds: int = 8 * 86400,
) -> None:
    """Store a one-time action link record (8-day TTL).

    ``sibling_id`` is the other button from the same email. Both share one task
    token, so using either link must invalidate the other.
    """
    get_dynamodb_table().put_item(
        Item={
            "PK": action_pk(action_id),
            "SK": "TOKEN",
            "actionId": action_id,
            "siblingId": sibling_id,
            "taskToken": task_token,
            "email": email.strip().lower(),
            "itemId": item_id,
            "action": action,
            "itemName": item_name,
            "expiryDate": expiry_date,
            "ttl": int(time.time()) + ttl_seconds,
        }
    )


def get_action_link(action_id: str) -> dict[str, Any] | None:
    """Fetch an action link record without consuming it."""
    resp = get_dynamodb_table().get_item(
        Key={"PK": action_pk(action_id), "SK": "TOKEN"}
    )
    return resp.get("Item")


def consume_action_link(action_id: str) -> dict[str, Any] | None:
    """Atomically delete and return an action link record so it can only be used once."""
    try:
        resp = get_dynamodb_table().delete_item(
            Key={"PK": action_pk(action_id), "SK": "TOKEN"},
            ReturnValues="ALL_OLD",
        )
        return resp.get("Attributes")
    except Exception:
        return None
