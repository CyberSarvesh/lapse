"""Unit tests for Lapse Lambda handlers."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from handlers import action_handler, compute_reminder, delete_item, extract_document, get_upload_url


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


class ApiHandlersTests(unittest.TestCase):
    def test_get_upload_url_rejects_bad_ext(self) -> None:
        res = get_upload_url.handler({"queryStringParameters": {"email": "a@b.com", "ext": "exe"}}, None)
        self.assertEqual(res["statusCode"], 400)

    def test_delete_item_soft_deletes(self) -> None:
        with (
            patch("handlers.delete_item.get_item", return_value={"itemId": "x1", "status": "ACTIVE"}),
            patch("handlers.delete_item.put_item") as mock_put,
        ):
            res = delete_item.handler(
                {"pathParameters": {"itemId": "x1"}, "queryStringParameters": {"email": "a@b.com"}},
                None,
            )
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


if __name__ == "__main__":
    unittest.main()
