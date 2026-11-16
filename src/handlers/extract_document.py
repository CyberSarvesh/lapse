"""Extract document metadata and expiry date using Amazon Bedrock (or local fallback)."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from common.aws_clients import get_bedrock_client, get_s3_client
from common.dates import is_past, parse_expiry
from common.db import (
    STATUS_NEEDS_DATE,
    get_item,
    get_upload_lookup,
    put_item,
    utc_now_iso,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

EXTRACTION_PROMPT = (
    "You are reading a photo or PDF of a document (warranty, insurance policy, "
    "passport, licence, subscription, or receipt). Extract the item details and "
    "the expiry/renewal date. If a warranty duration is given from a purchase date "
    "(e.g. '2-year warranty from 2025-03-01'), compute the expiry date.\n"
    "Respond ONLY with valid JSON matching this schema:\n"
    '{"found": true, "name": "short item name", "type": "warranty|insurance|passport|subscription|other", '
    '"provider": "company or issuer", "expiryDate": "YYYY-MM-DD"}\n'
    'If no expiry date can be determined, return {"found": false, "name": "short item name", '
    '"type": "other", "provider": "", "expiryDate": ""}.'
)

EXT_TO_FORMAT = {
    "jpg": ("image", "jpeg"),
    "jpeg": ("image", "jpeg"),
    "png": ("image", "png"),
    "webp": ("image", "webp"),
    "pdf": ("document", "pdf"),
}


def _parse_s3_event(event: dict[str, Any]) -> tuple[str, str]:
    """Return (bucket, key) from an EventBridge S3 Object Created event."""
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name") or os.environ["UPLOADS_BUCKET"]
    key = detail.get("object", {}).get("key", "")
    return bucket, key


def _extract_item_id_from_key(key: str) -> str:
    filename = key.rsplit("/", 1)[-1]
    return filename.split(".", 1)[0]


def _local_extract(s3_key: str, metadata: dict[str, str], body: bytes) -> dict[str, Any]:
    """Deterministic extraction for LocalStack testing without Bedrock."""
    if metadata.get("test-hint") == "unreadable" or body.startswith(b"UNREADABLE"):
        return {
            "found": False,
            "name": "Uploaded Document",
            "type": "other",
            "provider": "",
            "expiryDate": "",
        }
    expiry = metadata.get("expiry")
    if not expiry:
        expiry = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
    return {
        "found": True,
        "name": metadata.get("name", "Home Appliance Warranty"),
        "type": metadata.get("type", "warranty"),
        "provider": metadata.get("provider", "Acme Corp"),
        "expiryDate": expiry,
    }


def _bedrock_extract(s3_key: str, body: bytes) -> dict[str, Any]:
    """Call Amazon Bedrock Converse API with vision/document content."""
    ext = s3_key.rsplit(".", 1)[-1].lower() if "." in s3_key else "jpeg"
    kind, fmt = EXT_TO_FORMAT.get(ext, ("image", "jpeg"))

    if kind == "document":
        media_block = {"document": {"format": fmt, "name": "document", "source": {"bytes": body}}}
    else:
        media_block = {"image": {"format": fmt, "source": {"bytes": body}}}

    model_id = os.environ["BEDROCK_MODEL_ID"]
    resp = get_bedrock_client().converse(
        modelId=model_id,
        messages=[
            {
                "role": "user",
                "content": [media_block, {"text": EXTRACTION_PROMPT}],
            }
        ],
        inferenceConfig={"maxTokens": 300, "temperature": 0.0},
    )
    text = resp["output"]["message"]["content"][0]["text"].strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    parsed = json.loads(match.group(0) if match else text)
    expiry = str(parsed.get("expiryDate") or "").strip()
    valid_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry))
    return {
        "found": bool(parsed.get("found")) and valid_date,
        "name": str(parsed.get("name") or "Uploaded Document")[:120],
        "type": str(parsed.get("type") or "other")[:40],
        "provider": str(parsed.get("provider") or "")[:80],
        "expiryDate": expiry if valid_date else "",
    }


def _delete_document(bucket: str, key: str) -> None:
    """Best-effort delete; the uploads bucket lifecycle rule (1 day) is the backstop."""
    try:
        get_s3_client().delete_object(Bucket=bucket, Key=key)
    except Exception:
        logger.exception("Could not delete %s; lifecycle rule will remove it", key)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Extract document details or pass through pre-populated manual/continued items."""
    if event.get("skipExtract"):
        return {
            "found": True,
            "itemId": event["itemId"],
            "email": event["email"].strip().lower(),
            "name": event.get("name", "Untitled Item"),
            "type": event.get("type", "other"),
            "provider": event.get("provider", ""),
            "expiryDate": event["expiryDate"],
            "s3Key": event.get("s3Key", ""),
            "followUpCount": int(event.get("followUpCount", 0)),
        }

    bucket, s3_key = _parse_s3_event(event)
    item_id = _extract_item_id_from_key(s3_key)
    lookup = get_upload_lookup(item_id)
    if not lookup:
        raise ValueError(f"No upload lookup found for itemId={item_id}")
    email = lookup["email"]

    existing = get_item(email, item_id) or {}
    now = utc_now_iso()

    try:
        obj = get_s3_client().get_object(Bucket=bucket, Key=s3_key)
        body = obj["Body"].read()
        metadata = {k.lower(): v for k, v in (obj.get("Metadata") or {}).items()}
        if os.environ.get("LOCAL_MODE") == "true":
            extracted = _local_extract(s3_key, metadata, body)
        else:
            extracted = _bedrock_extract(s3_key, body)
    except Exception:
        # Never break the flow (D10), but make the failure visible in CloudWatch.
        logger.exception("Extraction failed for %s; falling back to NEEDS_DATE", s3_key)
        placeholder = str(existing.get("name") or "")
        extracted = {
            "found": False,
            "name": "Uploaded Document" if not placeholder or placeholder.startswith("Reading document") else placeholder,
            "type": existing.get("type") or "other",
            "provider": existing.get("provider") or "",
            "expiryDate": "",
        }
    finally:
        # Privacy (D27): once read, the photo is never needed again. Only the
        # extracted name, provider and date are kept.
        _delete_document(bucket, s3_key)

    # A document whose date has already passed (an expired policy, or the model
    # picking up an issue date) must not schedule a reminder for the past: ask
    # the user for the renewed date instead, and show when it expired.
    expired_on = ""
    if extracted["found"]:
        parsed = parse_expiry(extracted["expiryDate"])
        if parsed is None or is_past(parsed):
            expired_on = extracted["expiryDate"] if parsed else ""
            extracted = {**extracted, "found": False, "expiryDate": ""}

    if not extracted["found"]:
        put_item(
            {
                **existing,
                "PK": f"USER#{email}",
                "SK": f"ITEM#{item_id}",
                "itemId": item_id,
                "email": email,
                "name": extracted["name"],
                "type": extracted["type"],
                "provider": extracted["provider"],
                "expiryDate": "",
                "s3Key": "",
                "status": STATUS_NEEDS_DATE,
                "expiredOn": expired_on,
                "followUpCount": 0,
                "createdAt": existing.get("createdAt") or now,
                "updatedAt": now,
            }
        )

    return {
        "found": extracted["found"],
        "itemId": item_id,
        "email": email,
        "name": extracted["name"],
        "type": extracted["type"],
        "provider": extracted["provider"],
        "expiryDate": extracted["expiryDate"],
        "s3Key": "",
        "followUpCount": 0,
    }
