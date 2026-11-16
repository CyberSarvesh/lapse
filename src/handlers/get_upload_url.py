"""GET /upload-url — Create a PROCESSING item and return a presigned S3 PUT URL."""

from __future__ import annotations

import os
import uuid
from typing import Any

from common.auth import authenticated_email
from common.aws_clients import get_s3_client
from common.db import (
    STATUS_PROCESSING,
    item_sk,
    put_item,
    put_upload_lookup,
    user_pk,
    utc_now_iso,
)
from common.http import json_response

ALLOWED_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "pdf": "application/pdf",
}


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Generate a presigned S3 upload URL and seed the item + upload lookup."""
    params = event.get("queryStringParameters") or {}
    email = authenticated_email(event)
    if not email:
        return json_response(401, {"error": "Sign in first"})
    ext = (params.get("ext") or "jpg").strip().lower().lstrip(".")
    if ext not in ALLOWED_TYPES:
        return json_response(400, {"error": f"Unsupported file extension: {ext}"})

    item_id = uuid.uuid4().hex[:12]
    s3_key = f"uploads/{item_id}.{ext}"
    now = utc_now_iso()

    put_item(
        {
            "PK": user_pk(email),
            "SK": item_sk(item_id),
            "itemId": item_id,
            "email": email,
            "name": "Reading document…",
            "type": "other",
            "provider": "",
            "expiryDate": "",
            "s3Key": s3_key,
            "status": STATUS_PROCESSING,
            "followUpCount": 0,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    # How the uploader's locale writes dates, to read 03/04/2027 the right way round.
    date_order = (params.get("dateOrder") or "dmy").strip().lower()
    put_upload_lookup(item_id, email, s3_key, date_order=date_order if date_order in ("dmy", "mdy", "ymd") else "dmy")

    upload_url = get_s3_client().generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": os.environ["UPLOADS_BUCKET"],
            "Key": s3_key,
            "ContentType": ALLOWED_TYPES[ext],
        },
        ExpiresIn=300,
    )

    return json_response(200, {"uploadUrl": upload_url, "itemId": item_id, "s3Key": s3_key})
