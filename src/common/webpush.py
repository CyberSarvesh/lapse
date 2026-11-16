"""Minimal Web Push sender: RFC 8291 (aes128gcm message encryption) + RFC 8292 (VAPID).

Only needs ``cryptography`` (shipped to Lambda in the push layer). Each message
is encrypted for one browser subscription with a fresh key and salt, and the
request carries a short-lived ES256 token proving it comes from this server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.push import is_push_endpoint

RECORD_SIZE = 4096


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """aes128gcm body for one subscription (RFC 8291 §3.4, single record)."""
    ua_public_bytes = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    ua_public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public_bytes)
    as_private = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public_bytes = _public_bytes(as_private.public_key())
    salt = salt or os.urandom(16)

    shared = as_private.exchange(ec.ECDH(), ua_public)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public_bytes + as_public_bytes + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]

    if len(payload) + 17 > RECORD_SIZE:
        raise ValueError("Push payload too large")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_public_bytes)]) + as_public_bytes
    return header + ciphertext


def vapid_header(endpoint: str, private_key: ec.EllipticCurvePrivateKey, subject: str, ttl: int = 12 * 3600) -> str:
    """``Authorization: vapid t=<JWT>, k=<public key>`` for the endpoint's push service."""
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int(time.time()) + ttl, "sub": subject}
    signing_input = (
        b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        + "."
        + b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    der = private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    token = signing_input + "." + b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={token}, k={b64url(_public_bytes(private_key.public_key()))}"


def private_key_from_b64(value: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64url_decode(value), "big"), ec.SECP256R1())


def new_vapid_keys() -> tuple[str, str]:
    """(private, public) as base64url: 32-byte scalar, 65-byte uncompressed point."""
    key = ec.generate_private_key(ec.SECP256R1())
    d = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url(d), b64url(_public_bytes(key.public_key()))


@lru_cache(maxsize=1)
def _server_key() -> ec.EllipticCurvePrivateKey:
    import boto3

    name = os.environ["VAPID_PRIVATE_KEY_PARAM"]
    value = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    return private_key_from_b64(value.strip())


def send(subscription: dict[str, Any], message: dict[str, Any], subject: str, ttl: int = 86400) -> int:
    """POST one encrypted message; returns the push service's HTTP status (404/410 = gone)."""
    endpoint = subscription["endpoint"]
    if not is_push_endpoint(endpoint):
        return 400
    body = encrypt(json.dumps(message).encode("utf-8"), subscription["p256dh"], subscription["auth"])
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Encoding", "aes128gcm")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("TTL", str(ttl))
    req.add_header("Urgency", "high")
    req.add_header("Authorization", vapid_header(endpoint, _server_key(), subject))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        detail = err.read(300).decode("utf-8", "replace").strip()
        logging.getLogger().warning("Push service %s answered %s: %s", urlsplit(endpoint).hostname, err.code, detail)
        return err.code
