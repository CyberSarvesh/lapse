"""Expiry-date parsing shared by every place a date enters the system."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# A user far behind UTC can still be on "yesterday" by the server's clock, so a
# date one day before today (UTC) is accepted as today.
PAST_GRACE_DAYS = 1


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def parse_expiry(value: str) -> date | None:
    """Return the date for a real ``YYYY-MM-DD`` value (rejects e.g. 2026-02-31)."""
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def is_past(value: date, today: date | None = None) -> bool:
    return value < (today or today_utc()) - timedelta(days=PAST_GRACE_DAYS)


def check_future_expiry(value: str) -> str | None:
    """Return an error message for a missing, invalid or already-passed date, else None."""
    parsed = parse_expiry(value)
    if parsed is None:
        return "expiryDate must be a real date in YYYY-MM-DD format"
    if is_past(parsed):
        return "That date has already passed. Enter the date the new one expires."
    return None


def days_until(value: str, today: date | None = None) -> int | None:
    parsed = parse_expiry(value)
    return None if parsed is None else (parsed - (today or today_utc())).days


def describe_expiry(value: str, today: date | None = None) -> str:
    """Human phrase for emails: 'expires in 12 days (2026-11-28)', 'expired on 2026-11-06'."""
    days = days_until(value, today)
    if days is None:
        return f"expires {value}".strip()
    if days < 0:
        return f"expired on {value}"
    if days == 0:
        return f"expires today ({value})"
    if days == 1:
        return f"expires tomorrow ({value})"
    return f"expires in {days} days ({value})"
