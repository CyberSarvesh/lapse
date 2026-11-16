"""Demo accounts (D32): email-free, sample items, everything expires, push-only reminders."""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from botocore.exceptions import ClientError  # noqa: E402

from common import demo  # noqa: E402
from handlers import add_or_update_item, auth, send_reminder  # noqa: E402


class DemoAccountTests(unittest.TestCase):
    def test_demo_sign_in_creates_a_private_account_with_expiring_samples(self) -> None:
        table = MagicMock()
        written: list[dict] = []
        table.batch_writer.return_value.__enter__.return_value.put_item.side_effect = lambda Item: written.append(Item)
        with (
            patch("common.demo.get_dynamodb_table", return_value=table),
            patch("handlers.auth.create_session", return_value="sess") as create_session,
        ):
            res = auth.handler({"rawPath": "/auth/demo", "body": "{}"}, None)
        body = json.loads(res["body"])
        self.assertEqual(res["statusCode"], 200)
        self.assertTrue(body["demo"] and body["email"].endswith("@demo.lapse.invalid"))
        create_session.assert_called_once_with(body["email"], demo.DEMO_TTL_SECONDS)
        self.assertEqual(len(written), len(demo.SAMPLES))
        self.assertTrue(all(i["PK"] == f"USER#{body['email']}" for i in written))
        self.assertTrue(all(0 < i["ttl"] - time.time() <= demo.DEMO_TTL_SECONDS for i in written))
        self.assertEqual({i["status"] for i in written}, {"ACTIVE", "NEEDS_DATE", "RENEWED"})

    def test_two_demos_never_share_an_account(self) -> None:
        table = MagicMock()
        with patch("common.demo.get_dynamodb_table", return_value=table):
            self.assertNotEqual(demo.create_demo_account(), demo.create_demo_account())

    def test_rate_limit_answers_429(self) -> None:
        table = MagicMock()
        table.update_item.side_effect = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        with patch("common.demo.get_dynamodb_table", return_value=table):
            res = auth.handler({"rawPath": "/auth/demo", "body": "{}"}, None)
        self.assertEqual(res["statusCode"], 429)

    def test_no_sign_in_email_is_sent_to_a_demo_address(self) -> None:
        ses = MagicMock()
        with patch("handlers.auth.get_ses_client", return_value=ses), patch.dict(os.environ, {"APP_URL": "https://app"}):
            res = auth.handler({"rawPath": "/auth/start", "body": json.dumps({"email": "guest-1@demo.lapse.invalid"})}, None)
        self.assertEqual(res["statusCode"], 200)
        ses.send_email.assert_not_called()

    def test_items_added_by_a_demo_account_expire_too(self) -> None:
        def save(email: str) -> dict:
            with (
                patch("handlers.add_or_update_item.authenticated_email", return_value=email),
                patch("handlers.add_or_update_item.get_item", return_value={}),
                patch("handlers.add_or_update_item.put_item") as put,
                patch("handlers.add_or_update_item.get_sfn_client") as sfn,
                patch.dict(os.environ, {"STATE_MACHINE_ARN": "arn:sm"}),
            ):
                sfn.return_value.start_execution.return_value = {"executionArn": "arn:x"}
                add_or_update_item.handler({"body": json.dumps({"name": "X", "expiryDate": "2031-01-01"})}, None)
            return put.call_args[0][0]

        self.assertIn("ttl", save("guest-1@demo.lapse.invalid"))
        self.assertNotIn("ttl", save("real@example.com"))


class DemoReminderTests(unittest.TestCase):
    def _run(self, pushed: int) -> MagicMock:
        ses = MagicMock()
        with (
            patch.dict(os.environ, {"API_BASE_URL": "https://api", "APP_URL": "", "SENDER_EMAIL": "x@y"}),
            patch("handlers.send_reminder.put_action_link"),
            patch("handlers.send_reminder.get_ses_client", return_value=ses),
            patch("handlers.send_reminder.push.notify", return_value=pushed),
        ):
            send_reminder.handler({"taskToken": "t", "email": "guest-1@demo.lapse.invalid", "itemId": "i", "name": "Car",
                                   "type": "insurance", "expiryDate": "2030-01-01"}, None)
        return ses

    def test_demo_reminders_skip_email_and_go_by_push(self) -> None:
        self._run(pushed=1).send_email.assert_not_called()

    def test_demo_without_a_device_does_not_retry_forever(self) -> None:
        self._run(pushed=0)  # no exception: nothing to retry for a demo account


if __name__ == "__main__":
    unittest.main()
