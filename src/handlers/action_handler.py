"""GET /action & POST /action — Confirm-before-acting page for email buttons (D4, D5)."""

from __future__ import annotations

import base64
import html
import json
import re
from typing import Any
from urllib.parse import parse_qs

from botocore.exceptions import ClientError

from common.aws_clients import get_sfn_client
from common.db import STATUS_ACTIVE, consume_action_link, get_action_link, get_item
from common.http import html_response

STALE_TOKEN_ERRORS = {"TaskTimedOut", "InvalidToken", "TaskDoesNotExist"}


def _page(title: str, body_html: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)} — Lapse</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #f8fafc; color: #0f172a; margin: 0; padding: 32px 16px; }}
    .card {{ max-width: 460px; margin: 0 auto; background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.04); }}
    h1 {{ font-size: 20px; margin: 0 0 8px; }}
    p {{ color: #475569; line-height: 1.5; }}
    label {{ display: block; font-size: 14px; font-weight: 600; margin: 16px 0 6px; }}
    input[type="date"] {{ width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 15px; }}
    button {{ margin-top: 20px; width: 100%; padding: 12px; border: 0; border-radius: 8px; font-size: 15px; font-weight: 600; color: #fff; background: #16a34a; cursor: pointer; }}
    button.snooze {{ background: #2563eb; }}
  </style>
</head>
<body>
  <div class="card">{body_html}</div>
</body>
</html>"""


def _expired_response() -> dict[str, Any]:
    return html_response(
        410,
        _page(
            "Link Expired",
            "<h1>Link already used or expired</h1>"
            "<p>This reminder action has already been processed or is no longer valid.</p>",
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

    item_name = html.escape(record.get("itemName", "Your item"))
    expiry = html.escape(record.get("expiryDate", ""))
    action = record.get("action", "RENEWED")

    if action == "SNOOZE":
        body = (
            f"<h1>Snooze reminder for 7 days?</h1>"
            f"<p><strong>{item_name}</strong> (expires {expiry})</p>"
            f'<form method="POST" action="/action">'
            f'<input type="hidden" name="id" value="{html.escape(action_id)}">'
            f'<button type="submit" class="snooze">Confirm: Remind me in 7 days</button>'
            f"</form>"
        )
    else:
        body = (
            f"<h1>Mark as renewed?</h1>"
            f"<p><strong>{item_name}</strong> (expires {expiry})</p>"
            f'<form method="POST" action="/action">'
            f'<input type="hidden" name="id" value="{html.escape(action_id)}">'
            f'<label for="newExpiryDate">New expiry date (optional — restarts reminders for next cycle)</label>'
            f'<input id="newExpiryDate" type="date" name="newExpiryDate">'
            f'<button type="submit">Confirm: I renewed it</button>'
            f"</form>"
        )

    return html_response(200, _page("Confirm Action", body))


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
    new_expiry = (params.get("newExpiryDate") or "").strip()

    if new_expiry and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", new_expiry):
        return html_response(
            400,
            _page("Invalid Date", "<h1>Invalid date format</h1><p>Please use YYYY-MM-DD.</p>"),
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
                "Reminders Stopped",
                "<h1>Nothing to do</h1><p>Reminders for this item were already stopped or completed.</p>",
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

    item_name = html.escape(record.get("itemName", "Your item"))
    if choice_payload["choice"] == "RENEWED_WITH_DATE":
        msg = f"<p><strong>{item_name}</strong> updated to expire on <strong>{html.escape(new_expiry)}</strong>. We'll remind you next cycle.</p>"
    elif choice_payload["choice"] == "RENEWED":
        msg = f"<p><strong>{item_name}</strong> marked as renewed. Reminders stopped.</p>"
    else:
        msg = f"<p><strong>{item_name}</strong> snoozed. We'll remind you again soon.</p>"

    return html_response(200, _page("Confirmed", f"<h1>All set!</h1>{msg}"))


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
