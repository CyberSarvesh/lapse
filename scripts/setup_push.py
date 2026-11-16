"""Create the VAPID key pair for Web Push (once per AWS account and region).

Usage:
    python scripts/setup_push.py [--region us-east-1] [--name /lapse/vapid-private-key]

The private key goes into SSM Parameter Store as a SecureString and never
leaves AWS again; the public key is printed for the deploy:
    ... --parameter-overrides VapidPublicKey=<printed key>
Running it again reuses the existing key, so existing subscriptions keep working.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from common.webpush import b64url, new_vapid_keys, private_key_from_b64  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


def aws(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default=None)
    parser.add_argument("--name", default="/lapse/vapid-private-key")
    args = parser.parse_args()
    region = ["--region", args.region] if args.region else []

    got = aws("ssm", "get-parameter", "--name", args.name, "--with-decryption", *region)
    if got.returncode == 0:
        private = json.loads(got.stdout)["Parameter"]["Value"].strip()
        print(f"Using the existing key in {args.name}")
    else:
        private, _ = new_vapid_keys()
        put = aws("ssm", "put-parameter", "--name", args.name, "--type", "SecureString",
                  "--description", "Lapse Web Push (VAPID) private key", "--value", private, *region)
        if put.returncode:
            raise SystemExit(put.stderr)
        print(f"Created {args.name} (SecureString)")

    public = private_key_from_b64(private).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    print(f"VapidPublicKey={b64url(public)}")


if __name__ == "__main__":
    main()
