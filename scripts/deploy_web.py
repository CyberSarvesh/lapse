"""Upload the frontend to the stack's WebBucket with the API URL baked in.

Usage:
    python scripts/deploy_web.py --stack-name lapse
    python scripts/deploy_web.py --stack-name lapse-local --endpoint-url http://localhost:4566

Reads the ApiUrl / WebBucketName / WebUrl stack outputs, replaces the
``__LAPSE_API_URL__`` placeholder in web/index.html and uploads it, so the page
works without pasting the API URL into Connection settings.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3

PLACEHOLDER = "__LAPSE_API_URL__"
INDEX = Path(__file__).resolve().parents[1] / "web" / "index.html"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--endpoint-url", default=os.environ.get("AWS_ENDPOINT_URL"))
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    kwargs = {k: v for k, v in {"endpoint_url": args.endpoint_url, "region_name": args.region}.items() if v}
    cfn = boto3.client("cloudformation", **kwargs)
    outputs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cfn.describe_stacks(StackName=args.stack_name)["Stacks"][0].get("Outputs", [])
    }
    api_url, bucket = outputs["ApiUrl"], outputs["WebBucketName"]

    html = INDEX.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"{PLACEHOLDER} not found in {INDEX}")
    html = html.replace(PLACEHOLDER, api_url.rstrip("/"))

    boto3.client("s3", **kwargs).put_object(
        Bucket=bucket,
        Key="index.html",
        Body=html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="no-cache",
    )
    print(f"Uploaded index.html to s3://{bucket} (API: {api_url})")
    if "WebUrl" in outputs:
        print(f"Open: {outputs['WebUrl']}")


if __name__ == "__main__":
    main()
