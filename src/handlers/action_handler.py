"""GET /action & POST /action — Confirm-before-acting page for email buttons (D4, D5)."""

from __future__ import annotations

import base64
import os
import json
from typing import Any
from urllib.parse import parse_qs

from botocore.exceptions import ClientError

from common.aws_clients import get_sfn_client
from common.brand import e, nice_date, page_html, page_item_card
from common.dates import check_future_expiry, today_utc
from common.db import STATUS_ACTIVE, consume_action_link, get_action_link, get_item
from common.http import html_response

STALE_TOKEN_ERRORS = {"TaskTimedOut", "InvalidToken", "TaskDoesNotExist"}


def _app_url() -> str:
    # These pages are served from the API's own domain, which also serves the app at "/".
    return (os.environ.get("APP_URL") or "").rstrip("/") + "/"


QUICK_DATES_JS = """
document.querySelectorAll('[data-months]').forEach(function (b) {
  b.addEventListener('click', function () {
    var d = new Date(); d.setMonth(d.getMonth() + Number(b.dataset.months));
    var iso = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())).toISOString().slice(0, 10);
    document.getElementById('newExpiryDate').value = iso;
  });
});
"""


def _page(title: str, body: str, *, tone: str = "accent", badge: str = "clock", script: str = "") -> str:
    return page_html(title, body, tone=tone, badge=badge, app_url=_app_url(), script=script)


def _open_app_button(ghost: bool = False) -> str:
    return f'<a class="btn{" ghost" if ghost else ""}" href="{e(_app_url(), quote=True)}">Open Lapse</a>'


def _expired_response() -> dict[str, Any]:
    return html_response(
        410,
        _page(
            "Link already used",
            "<h1>This link has already been used</h1>"
            "<p>Each button in a reminder email works once, and only until the next reminder is sent. "
            "Open Lapse to see this item and change its date.</p>" + _open_app_button(),
            tone="gray",
            badge="link",
        ),
    )


def _handle_get(event: dict[str, Any]) -> dict[str, Any]:
    params = event.get("queryStringParameters") or {}
    action_id = (params.get("id") or "").strip()
    if not action_id:
        return _expired_response()

    record = get_action_link(action_id)
    if not record:
        return _expired_response()

    name = record.get("itemName", "Your item")
    expiry = record.get("expiryDate", "")
    item = get_item(record.get("email", ""), record.get("itemId", "")) if record.get("itemId") else None
    provider = (item or {}).get("provider", "")
    card = page_item_card(name, expiry, provider)
    hidden_id = f'<input type="hidden" name="id" value="{e(action_id, quote=True)}">'

    if record.get("action") == "SNOOZE":
        body = (
            "<h1>Remind you again in 7 days?</h1>"
            f"<p>We'll email you about <strong>{e(name)}</strong> again next week.</p>"
            f"{card}"
            f'<form method="POST" action="/action">{hidden_id}'
            '<button class="btn" type="submit">Yes, remind me in 7 days</button></form>'
            '<p class="note">This link works once.</p>'
        )
        return html_response(200, _page("Snooze reminder", body, badge="snooze"))

    body = (
        f"<h1>Did you renew {e(name)}?</h1>"
        "<p>Tell us when the new one expires and Lapse will remind you before that date too.</p>"
        f"{card}"
        f'<form method="POST" action="/action">{hidden_id}'
        '<label for="newExpiryDate">New expiry date</label>'
        f'<input id="newExpiryDate" type="date" name="newExpiryDate" required min="{today_utc().isoformat()}">'
        '<div class="quick">'
        '<button type="button" data-months="1">+1 month</button><button type="button" data-months="6">+6 months</button>'
        '<button type="button" data-months="12">+1 year</button><button type="button" data-months="24">+2 years</button>'
        '<button type="button" data-months="120">+10 years</button></div>'
        '<button class="btn" type="submit">Save date &amp; keep reminding me</button></form>'
        '<div class="or">or</div>'
        f'<form method="POST" action="/action">{hidden_id}<input type="hidden" name="mode" value="stop">'
        '<button class="btn ghost" type="submit">No new date, just stop reminders</button></form>'
        '<p class="note">This link works once.</p>'
    )
    return html_response(200, _page("Renewed?", body, tone="ok", badge="renew", script=QUICK_DATES_JS))


def _parse_post_params(event: dict[str, Any]) -> dict[str, str]:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if "application/json" in headers.get("content-type", ""):
        data = json.loads(raw or "{}")
        return {k: str(v) for k, v in data.items()}
    parsed = parse_qs(raw, keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items() if v}


def _handle_post(event: dict[str, Any]) -> dict[str, Any]:
    params = _parse_post_params(event)
    action_id = (params.get("id") or "").strip()
    # "stop" = renewed without a new date (the second button on the renew page).
    new_expiry = "" if params.get("mode") == "stop" else (params.get("newExpiryDate") or "").strip()

    date_error = check_future_expiry(new_expiry) if new_expiry else None
    if date_error:
        # The link is not consumed, so the user can go back and pick another date.
        return html_response(
            400,
            _page(
                "Check the date",
                f"<h1>Check the date</h1><p>{e(date_error)}</p>"
                '<button class="btn ghost" type="button" onclick="history.back()">Go back</button>',
                tone="warn",
                badge="alert",
            ),
        )

    if not action_id:
        return _expired_response()

    record = consume_action_link(action_id)
    if not record:
        return _expired_response()
    # Both buttons of one email share a task token: retire the other one too.
    if record.get("siblingId"):
        consume_action_link(record["siblingId"])

    # Stopped/renewed elsewhere since the email went out: don't revive the item.
    item = get_item(record.get("email", ""), record.get("itemId", "")) if record.get("itemId") else None
    if item is not None and item.get("status") != STATUS_ACTIVE:
        return html_response(
            410,
            _page(
                "Nothing to do",
                "<h1>Nothing to do</h1><p>Reminders for this item were already stopped or completed.</p>"
                + _open_app_button(),
                tone="gray",
                badge="check",
            ),
        )

    choice = record["action"]
    if choice == "RENEWED" and new_expiry:
        choice_payload = {"choice": "RENEWED_WITH_DATE", "newExpiryDate": new_expiry}
    elif choice == "RENEWED":
        choice_payload = {"choice": "RENEWED"}
    else:
        choice_payload = {"choice": "SNOOZE"}

    try:
        get_sfn_client().send_task_success(
            taskToken=record["taskToken"],
            output=json.dumps(choice_payload),
        )
    except ClientError as err:
        # The workflow already moved on (response window timed out, a newer email
        # was sent, or the execution ended). Treat it like a used link, not a 500.
        if err.response.get("Error", {}).get("Code") in STALE_TOKEN_ERRORS:
            return _expired_response()
        raise

    name = record.get("itemName", "Your item")
    if choice_payload["choice"] == "RENEWED_WITH_DATE":
        body = (
            "<h1>Saved. Your next reminder is set</h1>"
            f"<p><strong>{e(name)}</strong> now expires on <strong>{e(nice_date(new_expiry))}</strong>. "
            "Lapse will email you ahead of time.</p>"
            f"{page_item_card(name, new_expiry)}{_open_app_button()}"
        )
        page = _page("Saved", body, tone="ok", badge="check")
    elif choice_payload["choice"] == "RENEWED":
        body = (
            "<h1>Marked as renewed</h1>"
            f"<p>Reminders for <strong>{e(name)}</strong> have stopped. You can add it again any time from Lapse.</p>"
            f"{_open_app_button()}"
        )
        page = _page("Renewed", body, tone="ok", badge="check")
    else:
        body = (
            "<h1>Snoozed for 7 days</h1>"
            f"<p>We'll remind you about <strong>{e(name)}</strong> again next week.</p>"
            f"{_open_app_button()}"
        )
        page = _page("Snoozed", body, badge="snooze")
    return html_response(200, page)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Route GET /action and POST /action."""
    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "GET"
    ).upper()
    if method == "POST":
        return _handle_post(event)
    return _handle_get(event)
