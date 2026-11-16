"""Calendar subscription (D30).

GET  /calendar-feed      (signed in) -> {"url", "lastSyncedAt", "client"} (url null if none yet)
POST /calendar-feed      (signed in) -> same, creating the link; {"rotate": true} replaces it
GET  /calendar/{token}.ics           -> iCalendar feed of the user's active items

Calendar apps can't send a sign-in header, so the feed URL itself is the
secret: a random token stored only as a SHA-256 hash for lookup. Rotating it
cuts off every calendar that had the old link.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from common.auth import authenticated_email
from common.aws_clients import get_dynamodb_table
from common.brand import TYPE_LABELS
from common.dates import default_lead_days, parse_expiry
from common.db import STATUS_ACTIVE, list_user_items, user_pk
from common.http import json_response, parse_json_body

FEED_SK = "CALFEED"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _feed_pk(token_hash: str) -> str:
    return f"CALFEED#{token_hash}"


def _api_origin(event: dict[str, Any]) -> str:
    domain = (event.get("requestContext") or {}).get("domainName", "")
    if re.fullmatch(r"[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com", domain):
        return f"https://{domain}"
    # Always set by API Gateway; this function is not reachable any other way.
    raise RuntimeError("Request did not come through the Lapse API")


def _app_url(event: dict[str, Any]) -> str:
    return (os.environ.get("APP_URL") or _api_origin(event)).rstrip("/") + "/"


# ---------------------------------------------------------------------------
# Link management
# ---------------------------------------------------------------------------

def _status(event: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
    """What the page shows: the link (if any) and whether a calendar app is really fetching it."""
    if not record:
        return {"url": None, "lastSyncedAt": None, "client": None}
    synced = record.get("lastSyncedAt")
    return {
        "url": f"{_api_origin(event)}/calendar/{record['token']}.ics",
        "lastSyncedAt": int(synced) if synced is not None else None,
        "client": record.get("client"),
    }


def _get_or_create_link(event: dict[str, Any], method: str) -> dict[str, Any]:
    email = authenticated_email(event)
    if not email:
        return json_response(401, {"error": "Sign in first"})
    table = get_dynamodb_table()
    current = table.get_item(Key={"PK": user_pk(email), "SK": FEED_SK}).get("Item")
    if method == "GET":  # read-only: never creates a link just because the page looked
        return json_response(200, _status(event, current))

    if current and not parse_json_body(event).get("rotate"):
        return json_response(200, _status(event, current))
    if current:
        table.delete_item(Key={"PK": _feed_pk(current["tokenHash"]), "SK": "META"})
    token = secrets.token_urlsafe(24)
    record = {"PK": user_pk(email), "SK": FEED_SK, "token": token, "tokenHash": _hash(token)}
    table.put_item(Item={"PK": _feed_pk(_hash(token)), "SK": "META", "email": email})
    table.put_item(Item=record)
    return json_response(200, _status(event, record))


def calendar_client(user_agent: str) -> str | None:
    """Name of the calendar app fetching the feed, or None for a person opening it in a browser."""
    ua = user_agent or ""
    if "Google-Calendar-Importer" in ua or "Google" in ua and "Calendar" in ua:
        return "Google Calendar"
    if any(k in ua for k in ("dataaccessd", "CalendarAgent", "iOS/", "macOS/")):
        return "Apple Calendar"
    if any(k in ua for k in ("Outlook", "Microsoft Office", "MSOffice", "Exchange")):
        return "Outlook"
    if "Mozilla/" in ua:  # an ordinary browser tab, not a subscription
        return None
    return "Calendar app" if ua else None


def _record_sync(email: str, token_hash: str, client: str) -> None:
    try:
        get_dynamodb_table().update_item(
            Key={"PK": user_pk(email), "SK": FEED_SK},
            UpdateExpression="SET lastSyncedAt = :t, #c = :c",
            ConditionExpression="tokenHash = :h",  # only for the current link, never recreate a deleted one
            ExpressionAttributeNames={"#c": "client"},
            ExpressionAttributeValues={":t": int(time.time()), ":c": client, ":h": token_hash},
        )
    except Exception:  # noqa: BLE001 - a status hint must never break the feed
        pass


# ---------------------------------------------------------------------------
# iCalendar output
# ---------------------------------------------------------------------------

def _text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545: lines longer than 75 octets continue on the next line after a space."""
    out, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > 75:
            out.append(current.decode("utf-8"))
            current = b" " + b
        else:
            current += b
    out.append(current.decode("utf-8"))
    return "\r\n".join(out)


def build_calendar(items: list[dict[str, Any]], app_url: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Lapse//Renewal reminders//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Lapse renewals",
        "X-WR-CALDESC:Expiry dates you track in Lapse",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    for item in items:
        expiry = parse_expiry(str(item.get("expiryDate") or ""))
        if item.get("status") != STATUS_ACTIVE or expiry is None:
            continue
        name = str(item.get("name") or "Item")
        kind = TYPE_LABELS.get(str(item.get("type") or "other"), "Document")
        provider = str(item.get("provider") or "")
        lead = int(item.get("leadDays") or default_lead_days())
        details = " · ".join(x for x in (provider, kind) if x)
        description = f"{details}\nRenew it before it lapses.\nManage in Lapse: {app_url}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{item.get('itemId')}@lapse",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{expiry:%Y%m%d}",
            f"DTEND;VALUE=DATE:{expiry + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_text(f'{name} expires')}",
            f"DESCRIPTION:{_text(description)}",
            f"URL:{app_url}",
            f"CATEGORIES:{_text(kind)}",
            "TRANSP:TRANSPARENT",
        ]
        for days in sorted({lead, 1}, reverse=True):
            lines += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"TRIGGER:-P{days}D",
                f"DESCRIPTION:{_text(f'{name} expires in {days} day' + ('s' if days != 1 else ''))}",
                "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _serve_feed(event: dict[str, Any]) -> dict[str, Any]:
    raw = ((event.get("pathParameters") or {}).get("token") or "").removesuffix(".ics")
    not_found = {"statusCode": 404, "headers": {"Content-Type": "text/plain"}, "body": "Calendar not found"}
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", raw):
        return not_found
    record = get_dynamodb_table().get_item(Key={"PK": _feed_pk(_hash(raw)), "SK": "META"}).get("Item")
    if not record:
        return not_found
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    client = calendar_client(headers.get("user-agent", ""))
    if client:
        _record_sync(record["email"], _hash(raw), client)
    body = build_calendar(list_user_items(record["email"]), _app_url(event))
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/calendar; charset=utf-8",
            "Content-Disposition": 'inline; filename="lapse.ics"',
            "Cache-Control": "private, max-age=900",
            "X-Robots-Tag": "noindex",
        },
        "body": body,
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "GET").upper()
    path = (event.get("rawPath") or "").rstrip("/")
    if method == "POST" or path.endswith("/calendar-feed"):
        return _get_or_create_link(event, method)
    return _serve_feed(event)
