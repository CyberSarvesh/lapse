"""Extract document metadata and expiry date: Amazon Bedrock first, Amazon Textract OCR as the fallback."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from common.aws_clients import get_bedrock_client, get_s3_client, get_textract_client
from common.dates import is_past, parse_expiry, today_utc
from common.doc_reader import read_document
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


TEXTRACT_FORMATS = {"jpg", "jpeg", "png", "pdf"}  # synchronous DetectDocumentText (single-page PDF)


SMALL_WORDS = {"of", "and", "the", "for", "to", "in", "on", "by"}
SUFFIXES = {"ltd": "Ltd", "pvt": "Pvt", "inc": "Inc", "co": "Co", "llc": "LLC", "plc": "PLC"}


def _tidy(value: str) -> str:
    """'HDFC ERGO GENERAL INSURANCE COMPANY LTD' -> 'HDFC ERGO General Insurance Company Ltd'."""
    if not value.isupper():
        return value
    words = []
    for i, word in enumerate(value.split()):
        bare = word.lower().strip(".,")
        if bare in SUFFIXES:
            words.append(word.lower().replace(bare, SUFFIXES[bare]))
        elif bare in SMALL_WORDS and i:
            words.append(word.lower())
        elif len(bare) <= 4 and bare.isalpha():
            words.append(word)  # likely an acronym or brand: HDFC, ERGO, LIC, AXA
        else:
            words.append(word.capitalize())
    return " ".join(words)


def _textract_extract(s3_key: str, body: bytes, date_order: str) -> dict[str, Any]:
    """OCR the document with Textract and find the expiry date in its text lines."""
    ext = s3_key.rsplit(".", 1)[-1].lower()
    if ext not in TEXTRACT_FORMATS:
        raise ValueError(f"Textract can't read .{ext} files")
    resp = get_textract_client().detect_document_text(Document={"Bytes": body})
    lines = [b["Text"] for b in resp.get("Blocks", []) if b.get("BlockType") == "LINE"]
    reading = read_document(lines, today_utc(), date_order)
    logger.info("Textract read %d lines; expiry=%s confident=%s", len(lines), reading.expiry or "-", reading.confident)
    return {
        "found": reading.found,
        "confident": reading.confident,
        "name": _tidy(reading.name)[:120],
        "type": reading.type,
        "provider": _tidy(reading.provider)[:80],
        "expiryDate": reading.expiry,
    }


def _read_document(s3_key: str, body: bytes, date_order: str) -> dict[str, Any]:
    """Bedrock when the account allows it (it understands layout best), else Textract OCR."""
    if os.environ.get("BEDROCK_MODEL_ID"):
        try:
            result = _bedrock_extract(s3_key, body)
            if result["found"]:
                return {**result, "confident": True}
            logger.info("Bedrock found no expiry date in %s; trying Textract", s3_key)
        except Exception as err:  # e.g. model access not granted on this account
            logger.warning("Bedrock unavailable for %s (%s); using Textract", s3_key, err)
    return _textract_extract(s3_key, body, date_order)


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
            extracted = _read_document(s3_key, body, str(lookup.get("dateOrder") or "dmy"))
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
    suggested = ""
    if extracted["found"] and not extracted.get("confident", True):
        # A date with no "expires"/"valid until" label near it is only a guess:
        # show it to the user to confirm instead of scheduling on it.
        suggested = extracted["expiryDate"]
        extracted = {**extracted, "found": False, "expiryDate": ""}
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
                "suggestedDate": suggested if suggested and not is_past(parse_expiry(suggested)) else "",
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
