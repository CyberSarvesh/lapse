"""POST /push/subscribe, /push/unsubscribe, /push/test — this device's browser notifications (D31)."""

from __future__ import annotations

import os
import re
from typing import Any

from common.auth import authenticated_email
from common.http import json_response, parse_json_body
from common.push import delete_subscription, enabled, is_push_endpoint, notify, save_subscription

B64URL = re.compile(r"[A-Za-z0-9_-]+=*")


def push_subject(event: dict[str, Any]) -> str:
    """VAPID contact: the app's own address (not anyone's email)."""
    domain = (event.get("requestContext") or {}).get("domainName", "")
    return os.environ.get("APP_URL") or f"https://{domain}"


def _subscribe(email: str, body: dict[str, Any]) -> dict[str, Any]:
    sub = body.get("subscription") or {}
    endpoint = str(sub.get("endpoint") or "")
    keys = sub.get("keys") or {}
    p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
    if not (is_push_endpoint(endpoint) and len(endpoint) < 1024 and B64URL.fullmatch(p256dh) and B64URL.fullmatch(auth)):
        return json_response(400, {"error": "That doesn't look like a browser push subscription"})
    save_subscription(email, endpoint, p256dh, auth, str(body.get("device") or ""))
    return json_response(200, {"subscribed": True})


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    email = authenticated_email(event)
    if not email:
        return json_response(401, {"error": "Sign in first"})
    if not enabled():
        return json_response(503, {"error": "Notifications aren't set up on this Lapse"})
    path = (event.get("rawPath") or "").rstrip("/")
    body = parse_json_body(event)

    if path.endswith("/subscribe"):
        return _subscribe(email, body)
    if path.endswith("/unsubscribe"):
        endpoint = str(body.get("endpoint") or "")
        if endpoint:
            delete_subscription(email, endpoint)
        return json_response(200, {"subscribed": False})
    if path.endswith("/test"):
        app = push_subject(event).rstrip("/") + "/"
        sent = notify(email, {
            "title": "Notifications are on",
            "body": "Lapse will alert you here before anything you track expires.",
            "tag": "lapse-test",
            "url": app,
        }, push_subject(event))
        return json_response(200, {"sent": sent})
    return json_response(404, {"error": "Not found"})
