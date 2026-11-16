"""Browser push (D31): encryption, subscriptions, reminder fallback, and the files the worker needs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from common import push, webpush  # noqa: E402
from handlers import push as push_handler  # noqa: E402
from handlers import send_reminder, web_page  # noqa: E402

SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "BPx", "auth": "a1"}}


def _browser_decrypt(body: bytes, ua_key: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the browser does with an aes128gcm push message (RFC 8291, receiver side)."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_public = body[21:21 + idlen]
    ua_public = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    h = lambda k, d: hmac.new(k, d, hashlib.sha256).digest()  # noqa: E731
    ikm = h(h(auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = h(salt, ikm)
    plain = AESGCM(h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]).decrypt(
        h(prk, b"Content-Encoding: nonce\x00\x01")[:12], body[21 + idlen:], None)
    assert rs == 4096 and plain.endswith(b"\x02")
    return plain[:-1]


class WebPushCryptoTests(unittest.TestCase):
    def test_message_decrypts_like_a_browser_would(self) -> None:
        ua_key = ec.generate_private_key(ec.SECP256R1())
        ua_pub = ua_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        msg = json.dumps({"title": "Car Insurance expires in 12 days"}).encode()
        body = webpush.encrypt(msg, webpush.b64url(ua_pub), webpush.b64url(auth))
        self.assertEqual(_browser_decrypt(body, ua_key, auth), msg)

    def test_vapid_header_targets_the_push_service_origin(self) -> None:
        priv, pub = webpush.new_vapid_keys()
        header = webpush.vapid_header("https://fcm.googleapis.com/fcm/send/x", webpush.private_key_from_b64(priv), "https://app")
        claims = json.loads(base64.urlsafe_b64decode(header.split("t=")[1].split(".")[1] + "=="))
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        self.assertTrue(header.endswith(f"k={pub}"))

    def test_only_real_push_services_are_allowed(self) -> None:
        ok = ["https://fcm.googleapis.com/fcm/send/x", "https://web.push.apple.com/x", "https://db5.notify.windows.com/w/?t=x",
              "https://updates.push.services.mozilla.com/wpush/v2/x"]
        bad = ["https://evil.example/fcm.googleapis.com", "http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.example/x",
               "https://169.254.169.254/latest"]
        self.assertTrue(all(push.is_push_endpoint(u) for u in ok))
        self.assertFalse(any(push.is_push_endpoint(u) for u in bad))


class PushApiTests(unittest.TestCase):
    ENV = {"VAPID_PUBLIC_KEY": "pub", "VAPID_PRIVATE_KEY_PARAM": "/lapse/k"}

    def _call(self, path: str, body: dict) -> dict:
        return push_handler.handler({"rawPath": path, "body": json.dumps(body),
                                     "requestContext": {"domainName": "abc.execute-api.us-east-1.amazonaws.com"}}, None)

    def test_subscribe_stores_valid_and_rejects_other_urls(self) -> None:
        with (
            patch.dict(os.environ, self.ENV),
            patch("handlers.push.authenticated_email", return_value="a@b.com"),
            patch("handlers.push.save_subscription") as save,
        ):
            ok = self._call("/push/subscribe", {"subscription": SUB, "device": "Chrome on Windows"})
            bad = self._call("/push/subscribe", {"subscription": {**SUB, "endpoint": "https://evil.example/x"}})
        self.assertEqual((ok["statusCode"], bad["statusCode"]), (200, 400))
        save.assert_called_once_with("a@b.com", SUB["endpoint"], "BPx", "a1", "Chrome on Windows")

    def test_requires_sign_in(self) -> None:
        with patch.dict(os.environ, self.ENV), patch("handlers.push.authenticated_email", return_value=None):
            self.assertEqual(self._call("/push/subscribe", {"subscription": SUB})["statusCode"], 401)

    def test_dead_subscriptions_are_removed(self) -> None:
        subs = [{"endpoint": "https://fcm.googleapis.com/a"}, {"endpoint": "https://fcm.googleapis.com/b"}]
        fake_webpush = MagicMock()
        fake_webpush.send.side_effect = [201, 410]
        with (
            patch.dict(os.environ, self.ENV),
            patch.dict(sys.modules, {"common.webpush": fake_webpush}),
            patch("common.push.list_subscriptions", return_value=subs),
            patch("common.push.delete_subscription") as delete,
        ):
            # `from common import webpush` inside notify() picks the module attribute
            with patch.object(sys.modules["common"], "webpush", fake_webpush, create=True):
                self.assertEqual(push.notify("a@b.com", {"title": "t"}, "https://app"), 1)
        delete.assert_called_once_with("a@b.com", "https://fcm.googleapis.com/b")


class ReminderChannelTests(unittest.TestCase):
    EVENT = {"taskToken": "t", "email": "a@b.com", "itemId": "i", "name": "Car", "provider": "ACME",
             "type": "insurance", "expiryDate": "2030-01-01", "followUpCount": 0}

    def _run(self, email_ok: bool, pushed: int):
        ses = MagicMock()
        if not email_ok:
            ses.send_email.side_effect = RuntimeError("Email address is not verified")
        with (
            patch.dict(os.environ, {"API_BASE_URL": "https://api.example", "APP_URL": "", "SENDER_EMAIL": "x@y"}),
            patch("handlers.send_reminder.put_action_link"),
            patch("handlers.send_reminder.get_ses_client", return_value=ses),
            patch("handlers.send_reminder.push.notify", return_value=pushed) as notify,
        ):
            send_reminder.handler(dict(self.EVENT), None)
        return notify.call_args[0][1]

    def test_push_carries_the_same_one_time_buttons(self) -> None:
        msg = self._run(email_ok=True, pushed=1)
        self.assertTrue(msg["renewUrl"].startswith("https://api.example/action?id="))
        self.assertEqual([a["action"] for a in msg["actions"]], ["renew", "snooze"])

    def test_push_alone_is_enough_when_email_is_refused(self) -> None:
        self._run(email_ok=False, pushed=1)  # no exception: the reminder got out

    def test_fails_only_when_no_channel_worked(self) -> None:
        with self.assertRaises(RuntimeError):
            self._run(email_ok=False, pushed=0)


class WorkerFilesTests(unittest.TestCase):
    def test_worker_and_icons_are_served_from_the_root(self) -> None:
        web_page._cache.clear()
        s3 = MagicMock()
        s3.get_object.side_effect = lambda Bucket, Key: {"Body": MagicMock(read=lambda: b"\x89PNG" if Key.endswith(".png") else b"self.x=1")}
        with patch.dict(os.environ, {"WEB_BUCKET": "web"}), patch("handlers.web_page.get_s3_client", return_value=s3):
            sw = web_page.handler({"rawPath": "/sw.js"}, None)
            png = web_page.handler({"rawPath": "/icon-192.png"}, None)
        self.assertEqual(sw["headers"]["Service-Worker-Allowed"], "/")
        self.assertTrue(sw["headers"]["Content-Type"].startswith("text/javascript"))
        self.assertTrue(png["isBase64Encoded"])
        self.assertEqual(base64.b64decode(png["body"]), b"\x89PNG")


if __name__ == "__main__":
    unittest.main()
