"""Throwaway demo accounts, so people can try Lapse without receiving email (D32).

Each visitor gets a private address under ``demo.lapse.invalid`` (the .invalid
top-level domain can never receive mail), a 24-hour session and a set of sample
items covering every state the app shows. Everything the account creates
carries a DynamoDB ``ttl``, so it all disappears on its own a day later.
Reminders for demo accounts go out by browser push only.
"""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import timedelta
from typing import Any

from botocore.exceptions import ClientError

from common.aws_clients import get_dynamodb_table
from common.dates import today_utc
from common.db import STATUS_ACTIVE, STATUS_NEEDS_DATE, STATUS_RENEWED, item_sk, user_pk, utc_now_iso

DEMO_DOMAIN = "demo.lapse.invalid"
DEMO_TTL_SECONDS = 24 * 3600
MAX_DEMOS_PER_MINUTE = 20

# (name, type, provider, days from today, status, lead days, extra fields)
SAMPLES: list[tuple[str, str, str, int | None, str, int, dict[str, Any]]] = [
    ("Car insurance", "insurance", "Acme Insurance", 12, STATUS_ACTIVE, 30, {}),
    ("Home broadband", "subscription", "FiberNet", 5, STATUS_ACTIVE, 7, {}),
    ("Laptop extended warranty", "warranty", "TechMart", 75, STATUS_ACTIVE, 30, {}),
    ("Passport", "passport", "", 430, STATUS_ACTIVE, 90, {}),
    ("Driving licence", "passport", "Transport Office", 900, STATUS_ACTIVE, 60, {}),
    ("Health insurance", "insurance", "CareWell", -6, STATUS_ACTIVE, 30, {}),
    ("Gym membership", "subscription", "PulseFit", None, STATUS_NEEDS_DATE, 30, {"suggested": 200}),
    ("Phone plan", "subscription", "Telco One", 40, STATUS_RENEWED, 30, {}),
]


def is_demo(email: str) -> bool:
    return (email or "").lower().endswith("@" + DEMO_DOMAIN)


def demo_ttl(email: str) -> dict[str, int]:
    """``{"ttl": …}`` for records of a demo account, ``{}`` for real accounts."""
    return {"ttl": int(time.time()) + DEMO_TTL_SECONDS} if is_demo(email) else {}


def allow_demo() -> bool:
    """A global cap on new demo accounts per minute, so the button can't flood the table."""
    minute = int(time.time() // 60)
    try:
        get_dynamodb_table().update_item(
            Key={"PK": f"DEMORATE#{minute}", "SK": "META"},
            UpdateExpression="ADD n :one SET #t = :ttl",
            ConditionExpression="attribute_not_exists(n) OR n < :max",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":one": 1, ":max": MAX_DEMOS_PER_MINUTE, ":ttl": (minute + 2) * 60},
        )
        return True
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def create_demo_account() -> str:
    """Make a fresh demo address and fill it with sample items; returns the address."""
    email = f"guest-{secrets.token_hex(4)}@{DEMO_DOMAIN}"
    today = today_utc()
    now = utc_now_iso()
    ttl = int(time.time()) + DEMO_TTL_SECONDS
    with get_dynamodb_table().batch_writer() as batch:
        for name, kind, provider, days, status, lead, extra in SAMPLES:
            item_id = uuid.uuid4().hex[:12]
            item: dict[str, Any] = {
                "PK": user_pk(email),
                "SK": item_sk(item_id),
                "itemId": item_id,
                "email": email,
                "name": name,
                "type": kind,
                "provider": provider,
                "expiryDate": (today + timedelta(days=days)).isoformat() if days is not None else "",
                "s3Key": "",
                "status": status,
                "followUpCount": 0,
                "leadDays": lead,
                "createdAt": now,
                "updatedAt": now,
                "ttl": ttl,
            }
            if "suggested" in extra:
                item["suggestedDate"] = (today + timedelta(days=extra["suggested"])).isoformat()
            batch.put_item(Item=item)
    return email
