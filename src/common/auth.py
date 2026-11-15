"""Passwordless sign-in: one-time email links and DynamoDB-backed sessions (D23, D26).

Only SHA-256 hashes of tokens are stored, so a copy of the table cannot be
used to sign in. DynamoDB TTL deletes expired rows lazily (up to ~48 h late),
so every read also checks ``ttl`` itself.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any

from botocore.exceptions import ClientError

from common.aws_clients import get_dynamodb_table

LOGIN_TTL_SECONDS = 15 * 60
SESSION_TTL_SECONDS = 30 * 86400
LOGIN_RESEND_COOLDOWN_SECONDS = 60

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.fullmatch(email)) and len(email) <= 254


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _alive(record: dict[str, Any] | None) -> bool:
    return bool(record) and int(record.get("ttl", 0)) > int(time.time())


# --- Sign-in links ----------------------------------------------------------

def allow_login_email(email: str) -> bool:
    """At most one sign-in email per address per cooldown window (anti-spam)."""
    now = int(time.time())
    try:
        get_dynamodb_table().put_item(
            Item={
                "PK": f"LOGINRATE#{email}",
                "SK": "META",
                "sentAt": now,
                "ttl": now + LOGIN_RESEND_COOLDOWN_SECONDS,
            },
            ConditionExpression="attribute_not_exists(PK) OR sentAt < :cutoff",
            ExpressionAttributeValues={":cutoff": now - LOGIN_RESEND_COOLDOWN_SECONDS},
        )
        return True
    except ClientError as err:
        if err.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def create_login_token(email: str) -> str:
    token = secrets.token_urlsafe(32)
    get_dynamodb_table().put_item(
        Item={
            "PK": f"LOGIN#{_hash(token)}",
            "SK": "META",
            "email": email,
            "ttl": int(time.time()) + LOGIN_TTL_SECONDS,
        }
    )
    return token


def consume_login_token(token: str) -> str | None:
    """Delete the sign-in token and return its email, or None if unknown/expired/used."""
    if not token:
        return None
    resp = get_dynamodb_table().delete_item(
        Key={"PK": f"LOGIN#{_hash(token)}", "SK": "META"},
        ReturnValues="ALL_OLD",
    )
    record = resp.get("Attributes")
    return record["email"] if _alive(record) else None


# --- Sessions ---------------------------------------------------------------

def create_session(email: str) -> str:
    token = secrets.token_urlsafe(32)
    get_dynamodb_table().put_item(
        Item={
            "PK": f"SESSION#{_hash(token)}",
            "SK": "META",
            "email": email,
            "createdAt": int(time.time()),
            "ttl": int(time.time()) + SESSION_TTL_SECONDS,
        }
    )
    return token


def delete_session(token: str) -> None:
    if token:
        get_dynamodb_table().delete_item(Key={"PK": f"SESSION#{_hash(token)}", "SK": "META"})


def session_email(token: str) -> str | None:
    if not token:
        return None
    record = get_dynamodb_table().get_item(
        Key={"PK": f"SESSION#{_hash(token)}", "SK": "META"}
    ).get("Item")
    return record["email"] if _alive(record) else None


def bearer_token(event: dict[str, Any]) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    value = headers.get("authorization", "")
    return value[7:].strip() if value[:7].lower() == "bearer " else ""


def authenticated_email(event: dict[str, Any]) -> str | None:
    """The signed-in user's email for this request, or None.

    Every user-data route goes through this one check: the email always comes
    from the session, never from the request, so nobody can read or change
    another person's items by typing their address.
    """
    return session_email(bearer_token(event))
