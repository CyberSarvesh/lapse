"""Compute wait durations, 300-day continue-as-new flag, and response timeout."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

MAX_WAIT_SECONDS = 300 * 86400  # Leaves headroom under Step Functions 1-year limit (D13)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Return ``waitSeconds``, ``continueAsNew``, and ``responseTimeoutSeconds``."""
    demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
    lead_days = int(os.environ.get("LEAD_DAYS", "30"))
    mode = event.get("mode", "initial")

    if demo_mode:
        wait_map = {"initial": 90, "snooze": 60, "followup": 60}
        return {
            "waitSeconds": wait_map.get(mode, 60),
            "continueAsNew": False,
            "responseTimeoutSeconds": 180,
        }

    if mode == "snooze":
        wait_seconds = 7 * 86400
    elif mode == "followup":
        wait_seconds = 1 * 86400
    else:
        expiry_dt = datetime.strptime(event["expiryDate"], "%Y-%m-%d").replace(
            hour=9, minute=0, second=0, tzinfo=timezone.utc
        )
        reminder_dt = expiry_dt - timedelta(days=lead_days)
        now = datetime.now(timezone.utc)
        wait_seconds = max(1, int((reminder_dt - now).total_seconds()))

    continue_as_new = False
    if wait_seconds > MAX_WAIT_SECONDS:
        wait_seconds = MAX_WAIT_SECONDS
        continue_as_new = True

    return {
        "waitSeconds": wait_seconds,
        "continueAsNew": continue_as_new,
        "responseTimeoutSeconds": 3 * 86400,
    }
