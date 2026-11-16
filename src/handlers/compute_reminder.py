"""Compute wait durations, continue-as-new flag, and response timeout (D13)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

DAY = 86400
MAX_WAIT_SECONDS = 300 * DAY  # Single-wait cap; leaves headroom under the 1-year limit
EXECUTION_LIMIT_SECONDS = 365 * DAY  # Step Functions Standard maximum execution duration
SAFETY_MARGIN_SECONDS = 2 * DAY
RESPONSE_TIMEOUT_SECONDS = 3 * DAY


def _elapsed_seconds(execution_start_time: str | None, now: datetime) -> int:
    """Seconds this execution has already been running (0 if unknown)."""
    if not execution_start_time:
        return 0
    started = datetime.fromisoformat(execution_start_time.replace("Z", "+00:00"))
    return max(0, int((now - started).total_seconds()))


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Return ``waitSeconds``, ``continueAsNew``, and ``responseTimeoutSeconds``.

    The wait is capped so that, counting the time this execution has already run
    (snoozes and follow-ups add up), the wait plus the response window never
    reaches the 1-year execution limit. When capped, the workflow hands the item
    to a fresh execution after waking up.
    """
    demo_mode = os.environ.get("DEMO_MODE", "false").lower() == "true"
    lead_days = int(event.get("leadDays") or os.environ.get("LEAD_DAYS", "30"))
    mode = event.get("mode", "initial")

    if demo_mode:
        wait_map = {"initial": 90, "snooze": 60, "followup": 60}
        return {
            "waitSeconds": wait_map.get(mode, 60),
            "continueAsNew": False,
            "responseTimeoutSeconds": 180,
        }

    now = datetime.now(timezone.utc)
    if mode == "snooze":
        wait_seconds = 7 * DAY
    elif mode == "followup":
        wait_seconds = 1 * DAY
    else:
        expiry_dt = datetime.strptime(event["expiryDate"], "%Y-%m-%d").replace(
            hour=9, minute=0, second=0, tzinfo=timezone.utc
        )
        reminder_dt = expiry_dt - timedelta(days=lead_days)
        wait_seconds = max(1, int((reminder_dt - now).total_seconds()))

    budget = (
        EXECUTION_LIMIT_SECONDS
        - _elapsed_seconds(event.get("executionStartTime"), now)
        - RESPONSE_TIMEOUT_SECONDS
        - SAFETY_MARGIN_SECONDS
    )
    limit = min(MAX_WAIT_SECONDS, budget)

    continue_as_new = False
    if wait_seconds > limit:
        wait_seconds = max(1, limit)
        continue_as_new = True

    return {
        "waitSeconds": wait_seconds,
        "continueAsNew": continue_as_new,
        "responseTimeoutSeconds": RESPONSE_TIMEOUT_SECONDS,
    }
