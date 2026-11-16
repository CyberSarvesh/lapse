"""Unit tests for Lapse Lambda handlers."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from handlers import action_handler, add_or_update_item, auth, compute_reminder, delete_item, extract_document, get_upload_url, list_items


class ComputeReminderTests(unittest.TestCase):
    def test_demo_mode_durations(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "true", "LEAD_DAYS": "30"}):
            self.assertEqual(
                compute_reminder.handler({"expiryDate": "2030-01-01", "mode": "initial"}, None),
                {"waitSeconds": 90, "continueAsNew": False, "responseTimeoutSeconds": 180},
            )
            self.assertEqual(
                compute_reminder.handler({"expiryDate": "2030-01-01", "mode": "snooze"}, None)["waitSeconds"],
                60,
            )

    def test_far_future_triggers_continue_as_new(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "false", "LEAD_DAYS": "30"}):
            res = compute_reminder.handler({"expiryDate": "2035-01-01", "mode": "initial"}, None)
            self.assertTrue(res["continueAsNew"])
            self.assertEqual(res["waitSeconds"], 300 * 86400)


class ExtractDocumentTests(unittest.TestCase):
    def test_skip_extract_passthrough(self) -> None:
        res = extract_document.handler(
            {
                "skipExtract": True,
                "itemId": "abc123",
                "email": "User@Example.com",
                "name": "Passport",
                "expiryDate": "2028-05-01",
            },
            None,
        )
        self.assertTrue(res["found"])
        self.assertEqual(res["email"], "user@example.com")
        self.assertEqual(res["expiryDate"], "2028-05-01")

    def test_unreadable_marks_needs_date(self) -> None:
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: b"UNREADABLE"),
            "Metadata": {},
        }
        with (
            patch.dict(os.environ, {"LOCAL_MODE": "true", "UPLOADS_BUCKET": "uploads"}),
            patch("handlers.extract_document.get_upload_lookup", return_value={"email": "a@b.com"}),
            patch("handlers.extract_document.get_item", return_value={}),
            patch("handlers.extract_document.get_s3_client", return_value=mock_s3),
            patch("handlers.extract_document.put_item") as mock_put,
        ):
            res = extract_document.handler(
                {"detail": {"bucket": {"name": "uploads"}, "object": {"key": "uploads/item1.jpg"}}},
                None,
            )
        self.assertFalse(res["found"])
        mock_put.assert_called_once()
        saved = mock_put.call_args[0][0]
        self.assertEqual(saved["status"], "NEEDS_DATE")
        self.assertEqual(saved["s3Key"], "")
        # D27: the document is deleted even when extraction found nothing.
        mock_s3.delete_object.assert_called_once_with(Bucket="uploads", Key="uploads/item1.jpg")

    def test_document_deleted_after_successful_read(self) -> None:
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"img"), "Metadata": {"expiry": "2030-01-01"}}
        with (
            patch.dict(os.environ, {"LOCAL_MODE": "true", "UPLOADS_BUCKET": "uploads"}),
            patch("handlers.extract_document.get_upload_lookup", return_value={"email": "a@b.com"}),
            patch("handlers.extract_document.get_item", return_value={}),
            patch("handlers.extract_document.get_s3_client", return_value=mock_s3),
            patch("handlers.extract_document.put_item"),
        ):
            res = extract_document.handler(
                {"detail": {"bucket": {"name": "uploads"}, "object": {"key": "uploads/item2.png"}}}, None
            )
        self.assertTrue(res["found"])
        self.assertEqual(res["s3Key"], "")
        mock_s3.delete_object.assert_called_once_with(Bucket="uploads", Key="uploads/item2.png")


class ApiHandlersTests(unittest.TestCase):
    def test_get_upload_url_rejects_bad_ext(self) -> None:
        with patch("handlers.get_upload_url.authenticated_email", return_value="a@b.com"):
            res = get_upload_url.handler({"queryStringParameters": {"ext": "exe"}}, None)
        self.assertEqual(res["statusCode"], 400)

    def test_delete_item_soft_deletes(self) -> None:
        with (
            patch("handlers.delete_item.authenticated_email", return_value="a@b.com"),
            patch("handlers.delete_item.get_item", return_value={"itemId": "x1", "status": "ACTIVE"}) as mock_get,
            patch("handlers.delete_item.put_item") as mock_put,
        ):
            res = delete_item.handler(
                # An email in the query string is ignored: identity comes from the session.
                {"pathParameters": {"itemId": "x1"}, "queryStringParameters": {"email": "victim@x.com"}},
                None,
            )
        mock_get.assert_called_once_with("a@b.com", "x1")
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(mock_put.call_args[0][0]["status"], "DELETED")

    def test_action_handler_get_and_one_time_post(self) -> None:
        record = {
            "actionId": "tok1",
            "taskToken": "sfn-token",
            "action": "RENEWED",
            "itemName": "Car Insurance",
            "expiryDate": "2027-01-01",
        }
        with patch("handlers.action_handler.get_action_link", return_value=record):
            get_res = action_handler.handler(
                {"requestContext": {"http": {"method": "GET"}}, "queryStringParameters": {"id": "tok1"}},
                None,
            )
        self.assertEqual(get_res["statusCode"], 200)
        self.assertIn("newExpiryDate", get_res["body"])

        mock_sfn = MagicMock()
        with (
            patch("handlers.action_handler.consume_action_link", side_effect=[record, None]),
            patch("handlers.action_handler.get_sfn_client", return_value=mock_sfn),
        ):
            post_ok = action_handler.handler(
                {
                    "requestContext": {"http": {"method": "POST"}},
                    "headers": {"content-type": "application/x-www-form-urlencoded"},
                    "body": "id=tok1&newExpiryDate=2028-01-01",
                },
                None,
            )
            post_reused = action_handler.handler(
                {
                    "requestContext": {"http": {"method": "POST"}},
                    "headers": {"content-type": "application/x-www-form-urlencoded"},
                    "body": "id=tok1",
                },
                None,
            )
        self.assertEqual(post_ok["statusCode"], 200)
        self.assertEqual(post_reused["statusCode"], 410)
        mock_sfn.send_task_success.assert_called_once_with(
            taskToken="sfn-token",
            output=json.dumps({"choice": "RENEWED_WITH_DATE", "newExpiryDate": "2028-01-01"}),
        )


def _post(action_id: str) -> dict:
    return {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "body": f"id={action_id}",
    }


class ActionLinkSafetyTests(unittest.TestCase):
    record = {
        "actionId": "snz1",
        "siblingId": "ren1",
        "taskToken": "sfn-token",
        "action": "SNOOZE",
        "email": "a@b.com",
        "itemId": "item1",
        "itemName": "Car Insurance",
        "expiryDate": "2027-01-01",
    }

    def test_using_one_button_retires_the_other(self) -> None:
        mock_sfn = MagicMock()
        with (
            patch("handlers.action_handler.consume_action_link", side_effect=[self.record, {"actionId": "ren1"}]) as consume,
            patch("handlers.action_handler.get_item", return_value={"status": "ACTIVE"}),
            patch("handlers.action_handler.get_sfn_client", return_value=mock_sfn),
        ):
            res = action_handler.handler(_post("snz1"), None)
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual([c.args[0] for c in consume.call_args_list], ["snz1", "ren1"])

    def test_stale_task_token_shows_expired_page(self) -> None:
        from botocore.exceptions import ClientError

        mock_sfn = MagicMock()
        mock_sfn.send_task_success.side_effect = ClientError(
            {"Error": {"Code": "TaskTimedOut", "Message": "gone"}}, "SendTaskSuccess"
        )
        with (
            patch("handlers.action_handler.consume_action_link", side_effect=[self.record, None]),
            patch("handlers.action_handler.get_item", return_value={"status": "ACTIVE"}),
            patch("handlers.action_handler.get_sfn_client", return_value=mock_sfn),
        ):
            res = action_handler.handler(_post("snz1"), None)
        self.assertEqual(res["statusCode"], 410)

    def test_stopped_item_is_not_resumed(self) -> None:
        mock_sfn = MagicMock()
        with (
            patch("handlers.action_handler.consume_action_link", side_effect=[self.record, None]),
            patch("handlers.action_handler.get_item", return_value={"status": "DELETED"}),
            patch("handlers.action_handler.get_sfn_client", return_value=mock_sfn),
        ):
            res = action_handler.handler(_post("snz1"), None)
        self.assertEqual(res["statusCode"], 410)
        mock_sfn.send_task_success.assert_not_called()


class ExecutionLimitTests(unittest.TestCase):
    def test_wait_respects_time_already_spent_in_execution(self) -> None:
        from datetime import datetime, timedelta, timezone

        started = (datetime.now(timezone.utc) - timedelta(days=355)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        with patch.dict(os.environ, {"DEMO_MODE": "false", "LEAD_DAYS": "30"}):
            res = compute_reminder.handler({"mode": "snooze", "executionStartTime": started}, None)
        self.assertTrue(res["continueAsNew"])
        # 355 days spent + wait + 3-day response window must stay under 365 days.
        self.assertLess(355 * 86400 + res["waitSeconds"] + res["responseTimeoutSeconds"], 365 * 86400)

    def test_fresh_execution_snooze_is_not_capped(self) -> None:
        with patch.dict(os.environ, {"DEMO_MODE": "false", "LEAD_DAYS": "30"}):
            res = compute_reminder.handler({"mode": "snooze", "executionStartTime": "2099-01-01T00:00:00Z"}, None)
        self.assertEqual(res, {"waitSeconds": 7 * 86400, "continueAsNew": False, "responseTimeoutSeconds": 3 * 86400})


class StateMachineDefinitionTests(unittest.TestCase):
    def setUp(self) -> None:
        path = Path(__file__).resolve().parents[1] / "statemachine" / "lapse.asl.json"
        self.states = json.loads(path.read_text(encoding="utf-8"))["States"]

    def test_all_transitions_point_to_existing_states(self) -> None:
        targets = set()
        for state in self.states.values():
            targets.update(filter(None, [state.get("Next"), state.get("Default")]))
            targets.update(c["Next"] for c in state.get("Choices", []) + state.get("Catch", []))
        self.assertEqual(targets - set(self.states), set())

    def test_renewal_with_date_starts_a_fresh_execution(self) -> None:
        branch = self.states["BranchOnUserResponse"]["Choices"]
        target = next(c["Next"] for c in branch if c["StringEquals"] == "RENEWED_WITH_DATE")
        self.assertEqual(self.states[target]["Resource"], "arn:aws:states:::states:startExecution")
        self.assertTrue(self.states[target]["End"])

    def test_reminder_failures_do_not_fail_the_execution(self) -> None:
        catches = self.states["SendReminderAndWait"]["Catch"]
        self.assertEqual(catches[-1]["ErrorEquals"], ["States.ALL"])
        self.assertTrue(self.states["SendReminderAndWait"]["Retry"])


class StaleProcessingTests(unittest.TestCase):
    def test_old_processing_item_is_shown_as_needs_date(self) -> None:
        from common import db

        table = MagicMock()
        table.query.return_value = {
            "Items": [
                {"itemId": "old", "status": "PROCESSING", "name": "Reading document…", "createdAt": "2020-01-01T00:00:00Z"},
                {"itemId": "new", "status": "PROCESSING", "name": "Reading document…", "createdAt": db.utc_now_iso()},
            ]
        }
        with patch("common.db.get_dynamodb_table", return_value=table):
            items = {i["itemId"]: i for i in db.list_user_items("a@b.com")}
        self.assertEqual(items["old"]["status"], "NEEDS_DATE")
        self.assertEqual(items["old"]["name"], "Uploaded Document")
        self.assertEqual(items["new"]["status"], "PROCESSING")


class AuthTests(unittest.TestCase):
    def test_user_data_routes_reject_requests_without_a_session(self) -> None:
        anonymous = {
            "headers": {},
            "queryStringParameters": {"email": "victim@x.com", "ext": "jpg"},
            "pathParameters": {"itemId": "x1"},
            "body": json.dumps({"email": "victim@x.com", "expiryDate": "2030-01-01"}),
        }
        table = MagicMock()
        table.get_item.return_value = {}
        with patch("common.auth.get_dynamodb_table", return_value=table):
            for module in (list_items, get_upload_url, add_or_update_item, delete_item):
                with self.subTest(module=module.__name__):
                    self.assertEqual(module.handler(dict(anonymous), None)["statusCode"], 401)

    def test_session_lookup_uses_hashed_token_and_enforces_ttl(self) -> None:
        import time

        from common import auth as auth_lib

        table = MagicMock()
        table.get_item.return_value = {"Item": {"email": "a@b.com", "ttl": int(time.time()) + 60}}
        event = {"headers": {"Authorization": "Bearer secret-token"}}
        with patch("common.auth.get_dynamodb_table", return_value=table):
            self.assertEqual(auth_lib.authenticated_email(event), "a@b.com")
            key = table.get_item.call_args.kwargs["Key"]["PK"]
            self.assertNotIn("secret-token", key)
            table.get_item.return_value = {"Item": {"email": "a@b.com", "ttl": int(time.time()) - 1}}
            self.assertIsNone(auth_lib.authenticated_email(event))

    def test_start_sends_link_to_configured_app_url(self) -> None:
        ses = MagicMock()
        with (
            patch.dict(os.environ, {"APP_URL": "https://app.example", "SENDER_EMAIL": "no@reply", "LOCAL_MODE": "false"}),
            patch("handlers.auth.allow_login_email", return_value=True),
            patch("handlers.auth.create_login_token", return_value="tok"),
            patch("handlers.auth.get_ses_client", return_value=ses),
        ):
            res = auth.handler({"rawPath": "/auth/start", "body": json.dumps({"email": "A@B.com"})}, None)
        self.assertEqual(res["statusCode"], 200)
        self.assertNotIn("devLoginUrl", json.loads(res["body"]))
        sent = ses.send_email.call_args.kwargs
        self.assertEqual(sent["Destination"], {"ToAddresses": ["a@b.com"]})
        self.assertIn("https://app.example/?login=tok", sent["Message"]["Body"]["Text"]["Data"])

    def test_start_is_throttled_without_revealing_it(self) -> None:
        ses = MagicMock()
        with (
            patch.dict(os.environ, {"APP_URL": "https://app.example", "SENDER_EMAIL": "no@reply"}),
            patch("handlers.auth.allow_login_email", return_value=False),
            patch("handlers.auth.get_ses_client", return_value=ses),
        ):
            res = auth.handler({"rawPath": "/auth/start", "body": json.dumps({"email": "a@b.com"})}, None)
        self.assertEqual(res["statusCode"], 200)
        ses.send_email.assert_not_called()

    def test_verify_exchanges_token_once(self) -> None:
        with (
            patch("handlers.auth.consume_login_token", side_effect=["a@b.com", None]),
            patch("handlers.auth.create_session", return_value="sess"),
        ):
            ok = auth.handler({"rawPath": "/auth/verify", "body": json.dumps({"token": "t"})}, None)
            again = auth.handler({"rawPath": "/auth/verify", "body": json.dumps({"token": "t"})}, None)
        self.assertEqual(json.loads(ok["body"]), {"token": "sess", "email": "a@b.com"})
        self.assertEqual(again["statusCode"], 401)

    def test_expired_action_link_is_rejected_even_before_ttl_cleanup(self) -> None:
        import time

        from common import db

        table = MagicMock()
        table.get_item.return_value = {"Item": {"actionId": "x", "ttl": int(time.time()) - 5}}
        with patch("common.db.get_dynamodb_table", return_value=table):
            self.assertIsNone(db.get_action_link("x"))


class ExpiryDateTests(unittest.TestCase):
    """An expiry date must be real and not already passed (a reminder for the past is useless)."""

    def _add(self, expiry: str) -> dict:
        with (
            patch("handlers.add_or_update_item.authenticated_email", return_value="a@b.com"),
            patch("handlers.add_or_update_item.get_item", return_value={}),
            patch("handlers.add_or_update_item.put_item"),
            patch("handlers.add_or_update_item.get_sfn_client") as sfn,
            patch.dict(os.environ, {"STATE_MACHINE_ARN": "arn:sm"}),
        ):
            sfn.return_value.start_execution.return_value = {"executionArn": "arn:exec"}
            return add_or_update_item.handler({"body": json.dumps({"name": "Car", "expiryDate": expiry})}, None)

    def test_past_and_impossible_dates_are_rejected(self) -> None:
        from datetime import timedelta
        from common.dates import today_utc
        for bad in ((today_utc() - timedelta(days=10)).isoformat(), "2026-02-31", "soon"):
            with self.subTest(date=bad):
                res = self._add(bad)
                self.assertEqual(res["statusCode"], 400)

    def test_today_and_future_dates_are_accepted(self) -> None:
        from datetime import timedelta
        from common.dates import today_utc
        for good in (today_utc().isoformat(), (today_utc() + timedelta(days=30)).isoformat()):
            with self.subTest(date=good):
                self.assertEqual(self._add(good)["statusCode"], 200)

    def test_extracted_past_date_asks_for_the_renewed_date(self) -> None:
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"img"), "Metadata": {"expiry": "2020-01-01"}}
        with (
            patch.dict(os.environ, {"LOCAL_MODE": "true", "UPLOADS_BUCKET": "uploads"}),
            patch("handlers.extract_document.get_upload_lookup", return_value={"email": "a@b.com"}),
            patch("handlers.extract_document.get_item", return_value={}),
            patch("handlers.extract_document.get_s3_client", return_value=mock_s3),
            patch("handlers.extract_document.put_item") as mock_put,
        ):
            res = extract_document.handler(
                {"detail": {"bucket": {"name": "uploads"}, "object": {"key": "uploads/old1.png"}}}, None
            )
        self.assertFalse(res["found"])
        saved = mock_put.call_args[0][0]
        self.assertEqual(saved["status"], "NEEDS_DATE")
        self.assertEqual(saved["expiredOn"], "2020-01-01")

    def test_reminder_wording_matches_the_date(self) -> None:
        from datetime import date
        from common.dates import describe_expiry
        today = date(2026, 11, 16)
        self.assertEqual(describe_expiry("2026-11-06", today), "expired on 2026-11-06")
        self.assertEqual(describe_expiry("2026-11-16", today), "expires today (2026-11-16)")
        self.assertEqual(describe_expiry("2026-11-28", today), "expires in 12 days (2026-11-28)")


if __name__ == "__main__":
    unittest.main()
