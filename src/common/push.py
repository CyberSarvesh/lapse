"""Browser push subscriptions per user, and sending a message to all of a user's devices (D31)."""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any
from urllib.parse import urlsplit

from boto3.dynamodb.conditions import Key

from common.aws_clients import get_dynamodb_table
from common.db import user_pk
from common.demo import demo_ttl

logger = logging.getLogger()
GONE = {404, 410}  # the browser dropped the subscription
# Only send to real browser push services, never to an arbitrary URL a client supplied.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
PUSH_HOST_SUFFIXES = (".notify.windows.com", ".push.apple.com")


def is_push_endpoint(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES))


def enabled() -> bool:
    return bool(os.environ.get("VAPID_PUBLIC_KEY") and os.environ.get("VAPID_PRIVATE_KEY_PARAM"))


def _sk(endpoint: str) -> str:
    return "PUSH#" + hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:24]


def save_subscription(email: str, endpoint: str, p256dh: str, auth: str, device: str = "") -> None:
    get_dynamodb_table().put_item(
        Item={
            "PK": user_pk(email),
            "SK": _sk(endpoint),
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "device": device[:80],
            "createdAt": int(time.time()),
            **demo_ttl(email),
        }
    )


def delete_subscription(email: str, endpoint: str) -> None:
    get_dynamodb_table().delete_item(Key={"PK": user_pk(email), "SK": _sk(endpoint)})


def list_subscriptions(email: str) -> list[dict[str, Any]]:
    resp = get_dynamodb_table().query(
        KeyConditionExpression=Key("PK").eq(user_pk(email)) & Key("SK").begins_with("PUSH#")
    )
    return resp.get("Items", [])


def notify(email: str, message: dict[str, Any], subject: str) -> int:
    """Send ``message`` to every device of ``email``; returns how many accepted it."""
    if not enabled():
        return 0
    try:
        from common import webpush  # needs the push layer (cryptography)
    except ImportError:
        logger.warning("Push layer not available; skipping push")
        return 0
    delivered = 0
    for sub in list_subscriptions(email):
        try:
            status = webpush.send(sub, message, subject)
        except Exception:
            logger.exception("Push to %s failed", sub.get("device") or "device")
            continue
        if status in GONE:
            delete_subscription(email, sub["endpoint"])
        elif 200 <= status < 300:
            delivered += 1
        else:
            logger.warning("Push service answered %s", status)
    return delivered
