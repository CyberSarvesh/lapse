"""POST /items — Add a manual item or supply a date for a NEEDS_DATE item."""

from __future__ import annotations

import json
import os
import re
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
from common.http import json_response, parse_json_body


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Create or update an item with a known expiryDate and start its Step Functions workflow."""
    email = authenticated_email(event)
    if not email:
        return json_response(401, {"error": "Sign in first"})
    body = parse_json_body(event)
    expiry_date = (body.get("expiryDate") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry_date):
        return json_response(400, {"error": "expiryDate must be YYYY-MM-DD"})

    item_id = (body.get("itemId") or "").strip() or uuid.uuid4().hex[:12]
    existing = get_item(email, item_id) or {}
    now = utc_now_iso()

    item = {
        "PK": user_pk(email),
        "SK": item_sk(item_id),
        "itemId": item_id,
        "email": email,
        "name": (body.get("name") or existing.get("name") or "Untitled Item").strip(),
        "type": (body.get("type") or existing.get("type") or "other").strip(),
        "provider": (body.get("provider") or existing.get("provider") or "").strip(),
        "expiryDate": expiry_date,
        "s3Key": existing.get("s3Key", ""),
        "status": STATUS_ACTIVE,
        "followUpCount": 0,
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
    }
    exec_resp = get_sfn_client().start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        input=json.dumps(sfn_input),
    )
    item["executionArn"] = exec_resp["executionArn"]
    put_item(item)

    return json_response(200, {"item": item})
