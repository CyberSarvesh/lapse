"""Upload the frontend to the stack's WebBucket with the API URL baked in.

Usage:
    python scripts/deploy_web.py --stack-name lapse [--region us-east-1]
    python scripts/deploy_web.py --stack-name lapse-local --endpoint-url http://localhost:4566

Reads the ApiUrl / WebBucketName / WebUrl stack outputs, replaces the
``__LAPSE_API_URL__`` placeholder in web/index.html and uploads it, so the page
works without pasting the API URL into Connection settings.

Uses the AWS CLI rather than boto3 so it works with every credential source
the CLI supports (including ``aws login`` sessions).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

PLACEHOLDER = "__LAPSE_API_URL__"
VAPID_PLACEHOLDER = "__LAPSE_VAPID_KEY__"
WEB = Path(__file__).resolve().parents[1] / "web"
INDEX = WEB / "index.html"
# Other files the app serves (service worker, manifest, icons) and their types.
ASSETS = {
    "sw.js": "text/javascript; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json",
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
}


def aws(args: list[str], common: list[str]) -> str:
    return subprocess.run(["aws", *args, *common], check=True, capture_output=True, text=True).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stack-name", required=True)
    parser.add_argument("--endpoint-url", default=os.environ.get("AWS_ENDPOINT_URL"))
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    common: list[str] = []
    if args.endpoint_url:
        common += ["--endpoint-url", args.endpoint_url]
    if args.region:
        common += ["--region", args.region]

    stacks = json.loads(aws(["cloudformation", "describe-stacks", "--stack-name", args.stack_name, "--output", "json"], common))
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stacks["Stacks"][0].get("Outputs", [])}
    api_url, bucket = outputs["ApiUrl"], outputs["WebBucketName"]

    html = INDEX.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        raise SystemExit(f"{PLACEHOLDER} not found in {INDEX}")
    html = html.replace(PLACEHOLDER, api_url.rstrip("/"))
    html = html.replace(VAPID_PLACEHOLDER, outputs.get("VapidPublicKey", ""))

    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "index.html"
        page.write_text(html, encoding="utf-8")
        aws(
            [
                "s3", "cp", str(page), f"s3://{bucket}/index.html",
                "--content-type", "text/html; charset=utf-8",
                "--cache-control", "no-cache",
            ],
            common,
        )
    for name, content_type in ASSETS.items():
        if (WEB / name).exists():
            aws(["s3", "cp", str(WEB / name), f"s3://{bucket}/{name}", "--content-type", content_type], common)
    print(f"Uploaded index.html and {len(ASSETS)} assets to s3://{bucket} (API: {api_url})")
    print("Browser notifications:", "on" if outputs.get("VapidPublicKey") else "off (run scripts/setup_push.py)")
    if outputs.get("WebDistributionId"):
        # Drop CloudFront's cached copy so the new page is served right away.
        aws(
            [
                "cloudfront", "create-invalidation",
                "--distribution-id", outputs["WebDistributionId"],
                "--paths", "/", "/index.html",
            ],
            common,
        )
        print("Refreshed the CloudFront cache")
    if "WebUrl" in outputs:
        print(f"Open: {outputs['WebUrl']}")


if __name__ == "__main__":
    main()
