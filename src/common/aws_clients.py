"""Shared AWS SDK client and resource factories.

Respects ``AWS_ENDPOINT_URL`` when running inside LocalStack or local tests,
while using standard boto3 defaults in production AWS.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config


def _endpoint_kwargs() -> dict[str, Any]:
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    return {"endpoint_url": endpoint} if endpoint else {}


@lru_cache(maxsize=1)
def get_dynamodb_table():
    """Return the DynamoDB Table resource for ``TABLE_NAME``."""
    dynamodb = boto3.resource("dynamodb", **_endpoint_kwargs())
    return dynamodb.Table(os.environ["TABLE_NAME"])


@lru_cache(maxsize=1)
def get_s3_client():
    """Return a cached S3 client."""
    return boto3.client("s3", **_endpoint_kwargs())


@lru_cache(maxsize=1)
def get_sfn_client():
    """Return a cached Step Functions client."""
    return boto3.client("stepfunctions", **_endpoint_kwargs())


@lru_cache(maxsize=1)
def get_ses_client():
    """Return a cached SES client."""
    return boto3.client("ses", **_endpoint_kwargs())


@lru_cache(maxsize=1)
def get_bedrock_client():
    """Return a cached Bedrock Runtime client.

    The read timeout stays well under the 60 s Lambda timeout so a slow model call
    raises inside the handler (and falls back to NEEDS_DATE) instead of killing it.
    """
    return boto3.client(
        "bedrock-runtime",
        config=Config(read_timeout=40, connect_timeout=5, retries={"max_attempts": 2, "mode": "standard"}),
        **_endpoint_kwargs(),
    )


@lru_cache(maxsize=1)
def get_textract_client():
    """Return a cached Textract client (OCR fallback when Bedrock can't be used)."""
    return boto3.client(
        "textract",
        config=Config(read_timeout=30, connect_timeout=5, retries={"max_attempts": 2, "mode": "standard"}),
        **_endpoint_kwargs(),
    )
