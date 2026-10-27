from unittest.mock import patch

from django.test import SimpleTestCase

from common.models import Webhook


class WebhookTests(SimpleTestCase):
    def response(self, status):
        from requests import Response

        response = Response()
        response.status_code = status
        return response

    def test_success_does_not_retry(self):
        webhook = Webhook(url="https://example.com", retries=2)
        with (
            patch("requests.request", return_value=self.response(204)) as send,
            patch("common.models.time.sleep") as sleep,
        ):
            webhook.send_http({"ok": True})
        send.assert_called_once()
        sleep.assert_not_called()

    def test_http_failure_retries(self):
        webhook = Webhook(url="https://example.com", retries=1, delay=3)
        with (
            patch(
                "requests.request", side_effect=[self.response(503), self.response(200)]
            ) as send,
            patch("common.models.time.sleep") as sleep,
            self.assertLogs("common.models", level="WARNING"),
        ):
            webhook.send_http({"ok": True})
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_non_success_statuses_are_not_silently_accepted(self):
        webhook = Webhook(url="https://example.com", retries=1)
        for status in (302, 400, 500):
            with (
                self.subTest(status=status),
                patch("requests.request", return_value=self.response(status)) as send,
                patch("common.models.time.sleep") as sleep,
                self.assertLogs("common.models", level="WARNING"),
            ):
                webhook.send_http({"ok": True})
            self.assertEqual(send.call_count, 2)
            sleep.assert_called_once()
