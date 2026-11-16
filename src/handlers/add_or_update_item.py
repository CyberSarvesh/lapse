"""POST /items — Add a manual item or supply a date for a NEEDS_DATE item."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from common.auth import authenticated_email
from common.aws_clients import get_sfn_client
from common.db import (
    STATUS_ACTIVE,
    get_item,
    item_sk,
    put_item,
    user_pk,
    utc_now_iso,
)
from common.dates import check_future_expiry, lead_days
from common.http import clean_text, json_response, parse_json_body

MAX_NAME = 120  # matches the form's maxlength
MAX_TYPE = 40
MAX_ITEM_ID = 64


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Create or update an item with a known expiryDate and start its Step Functions workflow."""
    email = authenticated_email(event)
    if not email:
        return json_response(401, {"error": "Sign in first"})
    body = parse_json_body(event)
    expiry_date = clean_text(body.get("expiryDate"), 10)
    date_error = check_future_expiry(expiry_date)
    if date_error:
        return json_response(400, {"error": date_error})

    item_id = clean_text(body.get("itemId"), MAX_ITEM_ID)
    existing = (get_item(email, item_id) if item_id else None) or {}
    if not existing:
        # Never let the client choose the id of a new item.
        item_id = uuid.uuid4().hex[:12]
    now = utc_now_iso()

    item = {
        "PK": user_pk(email),
        "SK": item_sk(item_id),
        "itemId": item_id,
        "email": email,
        "name": clean_text(body.get("name"), MAX_NAME) or existing.get("name") or "Untitled Item",
        "type": clean_text(body.get("type"), MAX_TYPE).lower() or existing.get("type") or "other",
        # An explicitly empty provider clears it; a missing one keeps what was there.
        "provider": clean_text(body["provider"], MAX_NAME) if "provider" in body else existing.get("provider") or "",
        "expiryDate": expiry_date,
        "s3Key": existing.get("s3Key", ""),
        "status": STATUS_ACTIVE,
        "followUpCount": 0,
        "leadDays": lead_days(body.get("leadDays", existing.get("leadDays"))),
        "createdAt": existing.get("createdAt") or now,
        "updatedAt": now,
    }

    sfn_input = {
        "skipExtract": True,
        "itemId": item["itemId"],
        "email": item["email"],
        "name": item["name"],
        "type": item["type"],
        "provider": item["provider"],
        "expiryDate": item["expiryDate"],
        "s3Key": item["s3Key"],
        "followUpCount": 0,
        "leadDays": item["leadDays"],
    }
    exec_resp = get_sfn_client().start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        input=json.dumps(sfn_input),
    )
    item["executionArn"] = exec_resp["executionArn"]
    put_item(item)

    return json_response(200, {"item": item})
