"""Regression tests for SDK retry safety.

The client may retry reads and explicitly read-only retrieval POSTs, but must
not replay mutations when a timeout or 5xx leaves the operation outcome
ambiguous.
"""
import unittest
from unittest.mock import call, patch

import requests

from synapse_memory.client import SynapseClient, SynapseError


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, text=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body if body is not None else {}
        self.text = text if text is not None else str(self._body)
        self.ok = 200 <= status_code < 400

    def json(self):
        return self._body


class TestClientRetryPolicy(unittest.TestCase):
    def setUp(self):
        self.client = SynapseClient(
            base_url="http://synapse.test",
            api_key="syn_live_test",
            tenant_id="tenant-test",
            timeout=1,
            max_retries=2,
        )

    def tearDown(self):
        self.client.close()

    def test_get_retries_after_connection_timeout(self):
        expected = {"status": "ok"}
        with patch.object(
            self.client._session,
            "request",
            side_effect=[
                requests.Timeout("temporary timeout"),
                FakeResponse(200, {"success": True, "data": expected}),
            ],
        ) as request, patch("synapse_memory.client.time.sleep"):
            result = self.client._request("GET", "/health")

        self.assertEqual(result, expected)
        self.assertEqual(request.call_count, 2)

    def test_mutating_post_is_not_retried_after_timeout(self):
        with patch.object(
            self.client._session, "request", side_effect=requests.Timeout("timeout")
        ) as request:
            with self.assertRaisesRegex(SynapseError, "outcome is unknown") as caught:
                self.client.create_memory("test mutation")

        self.assertEqual(request.call_count, 1)
        self.assertFalse(caught.exception.retryable)

    def test_mutating_post_is_not_retried_after_retryable_status(self):
        with patch.object(
            self.client._session,
            "request",
            return_value=FakeResponse(503, text="temporarily unavailable"),
        ) as request:
            with self.assertRaisesRegex(SynapseError, "automatic retry suppressed") as caught:
                self.client.create_memory("test mutation")

        self.assertEqual(request.call_count, 1)
        self.assertEqual(caught.exception.status, 503)

    def test_read_only_query_post_can_retry(self):
        query_data = {
            "fused_context": "The plan is annual.",
            "injected_nodes_count": 1,
            "injected_nodes": [],
            "active_storage_backend": "pgvector",
        }
        with patch.object(
            self.client._session,
            "request",
            side_effect=[
                FakeResponse(503, text="temporarily unavailable"),
                FakeResponse(200, {"success": True, "data": query_data}),
            ],
        ) as request, patch("synapse_memory.client.time.sleep"):
            result = self.client.retrieve("which plan?")

        self.assertEqual(result.fused_context, "The plan is annual.")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], "POST")
        self.assertTrue(request.call_args_list[0].args[1].endswith("/query"))

    def test_retry_after_header_is_respected_for_safe_request(self):
        with patch.object(
            self.client._session,
            "request",
            side_effect=[
                FakeResponse(429, text="rate limited", headers={"Retry-After": "0.25"}),
                FakeResponse(200, {"success": True, "data": {"ready": True}}),
            ],
        ) as request, patch("synapse_memory.client.time.sleep") as sleep:
            result = self.client._request("GET", "/readiness")

        self.assertEqual(result, {"ready": True})
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_invalid_retry_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            SynapseClient(timeout=0)
        with self.assertRaises(ValueError):
            SynapseClient(max_retries=-1)


if __name__ == "__main__":
    unittest.main()
