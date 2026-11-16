"""Build the Lambda layer with ``cryptography`` for Web Push (run before deploying with push on).

Usage:
    python scripts/build_push_layer.py [--arch arm64|x86_64]

Downloads Linux wheels for the Lambda runtime into layers/push/python, which
`sam build` / `aws cloudformation package` zips as the PushLayer.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "layers" / "push" / "python"
PLATFORMS = {
    "arm64": ["manylinux_2_28_aarch64", "manylinux2014_aarch64"],
    "x86_64": ["manylinux_2_28_x86_64", "manylinux2014_x86_64"],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arch", choices=sorted(PLATFORMS), default="arm64")
    args = parser.parse_args()

    shutil.rmtree(TARGET, ignore_errors=True)
    TARGET.mkdir(parents=True)
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(TARGET),
           "--implementation", "cp", "--python-version", "3.13", "--only-binary=:all:"]
    for platform in PLATFORMS[args.arch]:
        cmd += ["--platform", platform]
    subprocess.run([*cmd, "cryptography>=44,<51"], check=True)
    for junk in TARGET.glob("*.dist-info"):
        shutil.rmtree(junk)
    size = sum(f.stat().st_size for f in TARGET.rglob("*") if f.is_file()) / 1e6
    print(f"Built {TARGET} for {args.arch} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
