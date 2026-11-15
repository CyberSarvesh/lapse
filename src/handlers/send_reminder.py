"""Create one-time action links and send a reminder email via Amazon SES."""

from __future__ import annotations

import html
import os
import secrets
from typing import Any

from common.aws_clients import get_ses_client
from common.db import put_action_link


def handler(event: dict[str, Any], _context: Any) -> None:
    """Store one-time action tokens in DynamoDB and email the user."""
    task_token = event["taskToken"]
    email = event["email"].strip().lower()
    item_id = event["itemId"]
    item_name = event.get("name", "Your item")
    provider = event.get("provider", "")
    expiry_date = event.get("expiryDate", "")
    follow_up_count = int(event.get("followUpCount", 0))

    renew_id = secrets.token_urlsafe(8)
    snooze_id = secrets.token_urlsafe(8)

    put_action_link(renew_id, task_token, email, item_id, "RENEWED", item_name, expiry_date)
    put_action_link(snooze_id, task_token, email, item_id, "SNOOZE", item_name, expiry_date)

    api_base = os.environ["API_BASE_URL"].rstrip("/")
    renew_url = f"{api_base}/action?id={renew_id}"
    snooze_url = f"{api_base}/action?id={snooze_id}"

    prefix = f"[Follow-up #{follow_up_count}] " if follow_up_count > 0 else ""
    subject = f"{prefix}Lapse reminder: {item_name} expires {expiry_date}"

    safe_name = html.escape(item_name)
    safe_provider = html.escape(provider)
    safe_expiry = html.escape(expiry_date)
    provider_line = f" ({safe_provider})" if safe_provider else ""

    html_body = f"""<!doctype html>
<html>
<body style="font-family:system-ui,-apple-system,sans-serif;max-width:540px;margin:24px auto;color:#1f2937;">
  <h2 style="margin-bottom:8px;">{safe_name}{provider_line}</h2>
  <p style="font-size:16px;">Expires on <strong>{safe_expiry}</strong>.</p>
  <div style="margin:24px 0;">
    <a href="{renew_url}" style="display:inline-block;background:#16a34a;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none;font-weight:600;margin-right:12px;">I renewed it</a>
    <a href="{snooze_url}" style="display:inline-block;background:#2563eb;color:#fff;padding:12px 20px;border-radius:6px;text-decoration:none;font-weight:600;">Remind me in 7 days</a>
  </div>
  <p style="font-size:12px;color:#6b7280;">Each link works once and opens a confirmation page before updating your reminder.</p>
</body>
</html>"""

    text_body = (
        f"{item_name} expires on {expiry_date}.\n\n"
        f"I renewed it: {renew_url}\n"
        f"Remind me in 7 days: {snooze_url}\n"
    )

    get_ses_client().send_email(
        Source=os.environ["SENDER_EMAIL"],
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text_body, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        },
    )
