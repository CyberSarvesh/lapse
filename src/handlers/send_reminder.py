"""Create one-time action links and send a reminder email via Amazon SES."""

from __future__ import annotations

import os
import secrets
from typing import Any

from common.aws_clients import get_ses_client
from common.brand import e, email_button, email_html, email_item_card, nice_date
from common.dates import days_until, describe_expiry
from common.db import put_action_link


def _app_url() -> str:
    return (os.environ.get("APP_URL") or os.environ["API_BASE_URL"]).rstrip("/") + "/"


def _copy(item_name: str, expiry: str, follow_up: int) -> tuple[str, str, str]:
    """(subject, heading, preheader) worded for how close the date is."""
    days = days_until(expiry)
    if days is not None and days < 0:
        heading = f"{item_name} expired on {nice_date(expiry)}"
        pre = "Renewed it? Add the new date in one tap so Lapse can track the next one."
    elif days == 0:
        heading = f"{item_name} expires today"
        pre = "Last chance to renew. Already done? Tell Lapse in one tap."
    else:
        heading = f"{item_name} expires in {days} day{'s' if days != 1 else ''}" if days is not None else f"{item_name} is due"
        pre = f"Due {nice_date(expiry)}. Renew it, or snooze this reminder for a week."
    prefix = f"Reminder {follow_up + 1}: " if follow_up > 0 else ""
    subject = heading if days is None or days < 0 else f"{heading} · {nice_date(expiry)}"
    return f"{prefix}{subject}", heading, pre


def handler(event: dict[str, Any], _context: Any) -> None:
    """Store one-time action tokens in DynamoDB and email the user."""
    task_token = event["taskToken"]
    email = event["email"].strip().lower()
    item_id = event["itemId"]
    item_name = event.get("name", "Your item")
    provider = event.get("provider", "")
    kind = event.get("type", "other")
    expiry_date = event.get("expiryDate", "")
    follow_up_count = int(event.get("followUpCount", 0))

    renew_id = secrets.token_urlsafe(8)
    snooze_id = secrets.token_urlsafe(8)

    put_action_link(renew_id, task_token, email, item_id, "RENEWED", item_name, expiry_date, sibling_id=snooze_id)
    put_action_link(snooze_id, task_token, email, item_id, "SNOOZE", item_name, expiry_date, sibling_id=renew_id)

    api_base = os.environ["API_BASE_URL"].rstrip("/")
    renew_url = f"{api_base}/action?id={renew_id}"
    snooze_url = f"{api_base}/action?id={snooze_id}"
    app_url = _app_url()

    subject, heading, preheader = _copy(item_name, expiry_date, follow_up_count)

    intro = (
        "We haven't heard back about this one, so here's another nudge. "
        if follow_up_count > 0 else ""
    ) + "Renewed it already? Tell us and add the new date, and Lapse will remind you next time too."

    content = f"""
<div style="margin:22px 0 22px;">{email_item_card(item_name, provider, kind, expiry_date)}</div>
<div>{email_button(renew_url, "I renewed it")}{email_button(snooze_url, "Remind me in 7 days", primary=False)}</div>
<p style="margin:12px 0 0;font-size:13px;line-height:1.5;color:#8b9592;">Each button opens a confirmation page first. Nothing changes until you confirm.</p>"""

    footer = (
        f'You get this because you track <strong>{e(item_name)}</strong> on Lapse. '
        f'<a href="{e(app_url, quote=True)}" style="color:#0f766e;">Open Lapse</a> to change the date or stop reminders.'
    )
    html_body = email_html(preheader=preheader, heading=heading, intro=e(intro), content=content, footer=footer)

    text_body = (
        f"{heading}.\n"
        f"{item_name}{f' ({provider})' if provider else ''} {describe_expiry(expiry_date)}.\n\n"
        f"I renewed it: {renew_url}\n"
        f"Remind me in 7 days: {snooze_url}\n\n"
        f"Each link opens a confirmation page first.\n"
        f"Open Lapse: {app_url}\n"
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
