"""POST /auth/start, /auth/verify, /auth/logout — passwordless email sign-in (D26)."""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any
from urllib.parse import urlencode

from common.auth import (
    allow_login_email,
    bearer_token,
    consume_login_token,
    create_login_token,
    create_session,
    delete_session,
    is_valid_email,
    normalize_email,
)
from common.aws_clients import get_ses_client
from common.http import json_response, parse_json_body

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _app_url(event: dict[str, Any]) -> str:
    """Where the sign-in link points: deploy config, else this API's own HTTPS address.

    Never a value the caller controls. ``requestContext.domainName`` is set by API
    Gateway to the API's own domain (execute-api or a mapped custom domain), not
    copied from the Host header, so nobody can make Lapse email a link to their site.
    """
    configured = os.environ.get("APP_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    domain = (event.get("requestContext") or {}).get("domainName", "")
    if not re.fullmatch(r"[a-z0-9]+\.execute-api\.[a-z0-9-]+\.amazonaws\.com", domain):
        raise RuntimeError("APP_URL is not configured")
    return f"https://{domain}"


def _sign_in_url(event: dict[str, Any], token: str) -> str:
    return f"{_app_url(event)}/?{urlencode({'login': token})}"


def _send_sign_in_email(email: str, url: str) -> None:
    safe_url = html.escape(url, quote=True)
    get_ses_client().send_email(
        Source=os.environ["SENDER_EMAIL"],
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": "Your Lapse sign-in link", "Charset": "UTF-8"},
            "Body": {
                "Text": {
                    "Data": f"Sign in to Lapse: {url}\n\nThe link works once and expires in 15 minutes. "
                    "If you didn't ask for it, ignore this email.\n",
                    "Charset": "UTF-8",
                },
                "Html": {
                    "Data": f"""<!doctype html><html><body style="font-family:system-ui,-apple-system,sans-serif;max-width:540px;margin:24px auto;color:#1f2937;">
  <h2>Sign in to Lapse</h2>
  <p><a href="{safe_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none;font-weight:600;">Sign in</a></p>
  <p style="font-size:12px;color:#6b7280;">The link works once and expires in 15 minutes. If you didn't ask for it, ignore this email.</p>
</body></html>""",
                    "Charset": "UTF-8",
                },
            },
        },
    )


def _start(event: dict[str, Any]) -> dict[str, Any]:
    email = normalize_email(parse_json_body(event).get("email"))
    if not is_valid_email(email):
        return json_response(400, {"error": "Enter a valid email address"})

    body: dict[str, Any] = {"sent": True}
    # Same answer whether or not we actually sent, so the endpoint can't be used
    # to probe who uses Lapse or to flood someone's inbox.
    if allow_login_email(email):
        url = _sign_in_url(event, create_login_token(email))
        _send_sign_in_email(email, url)
        if os.environ.get("LOCAL_MODE") == "true":
            body["devLoginUrl"] = url  # LocalStack doesn't deliver mail
    else:
        logger.info("Sign-in email throttled for %s", email)
    return json_response(200, body)


def _verify(event: dict[str, Any]) -> dict[str, Any]:
    token = str(parse_json_body(event).get("token") or "")
    email = consume_login_token(token)
    if not email:
        return json_response(401, {"error": "This sign-in link has expired or was already used. Request a new one."})
    return json_response(200, {"token": create_session(email), "email": email})


def _logout(event: dict[str, Any]) -> dict[str, Any]:
    delete_session(bearer_token(event))
    return json_response(200, {"signedOut": True})


ROUTES = {"/auth/start": _start, "/auth/verify": _verify, "/auth/logout": _logout}


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path", "")
    route = ROUTES.get(path.rstrip("/"))
    if not route:
        return json_response(404, {"error": "Not found"})
    return route(event)
