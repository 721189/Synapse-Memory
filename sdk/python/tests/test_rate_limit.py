"""Unit tests for the distributed-safe rate limiter.

Covers the in-process sliding window, the Redis-backed limiter (via a fake
client — no live Redis required), the automatic fallback behaviour, and the
factory's selection logic (REDIS_URL set/unset, package missing, unreachable).
"""

import sys
import types
import unittest
from unittest import mock

from synapse_memory.core.rate_limiter import (
    InProcessRateLimiter,
    RedisRateLimiter,
    build_rate_limiter,
)


class _FakeScript:
    def __init__(self, result=1, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def __call__(self, keys=None, args=None):
        self.calls.append((keys, args))
        if self.exc is not None:
            raise self.exc
        return self.result


class _FakeClient:
    """Minimal stand-in for redis.Redis used by RedisRateLimiter."""

    def __init__(self, script_result=1, script_exc=None, register_exc=None):
        self.script = _FakeScript(script_result, script_exc)
        self._register_exc = register_exc
        self.pinged = False

    def register_script(self, lua):
        if self._register_exc is not None:
            raise self._register_exc
        self.script.lua = lua
        return self.script

    def ping(self):
        self.pinged = True
        return True


class TestInProcessRateLimiter(unittest.TestCase):
    def test_allows_up_to_limit_then_denies(self):
        lim = InProcessRateLimiter()
        self.assertTrue(lim.check("k", 2, 60))
        self.assertTrue(lim.check("k", 2, 60))
        self.assertFalse(lim.check("k", 2, 60))

    def test_window_expiry_recovers_capacity(self):
        lim = InProcessRateLimiter()
        with mock.patch("synapse_memory.core.rate_limiter.time.time", return_value=1000.0):
            self.assertTrue(lim.check("k", 1, 60))
            self.assertFalse(lim.check("k", 1, 60))
        # 61s later the earlier timestamp has fallen out of the window.
        with mock.patch("synapse_memory.core.rate_limiter.time.time", return_value=1061.0):
            self.assertTrue(lim.check("k", 1, 60))

    def test_keys_are_independent(self):
        lim = InProcessRateLimiter()
        self.assertTrue(lim.check("a", 1, 60))
        self.assertFalse(lim.check("a", 1, 60))
        self.assertTrue(lim.check("b", 1, 60))

    def test_non_positive_limit_denies_and_evicts(self):
        lim = InProcessRateLimiter()
        self.assertFalse(lim.check("k", 0, 60))
        # The bucket is evicted so a hostile key space cannot grow unbounded.
        self.assertNotIn("k", lim._buckets)

    def test_window_seconds_parameter_is_respected(self):
        lim = InProcessRateLimiter()
        with mock.patch("synapse_memory.core.rate_limiter.time.time", return_value=100.0):
            self.assertTrue(lim.check("k", 1, 5))
            self.assertFalse(lim.check("k", 1, 5))
        with mock.patch("synapse_memory.core.rate_limiter.time.time", return_value=106.0):
            self.assertTrue(lim.check("k", 1, 5))


class TestRedisRateLimiter(unittest.TestCase):
    def test_allowed_when_script_returns_one(self):
        lim = RedisRateLimiter(_FakeClient(script_result=1))
        self.assertTrue(lim.check("k", 5, 60))
        self.assertEqual(lim.backend, "redis")

    def test_denied_when_script_returns_zero(self):
        lim = RedisRateLimiter(_FakeClient(script_result=0))
        self.assertFalse(lim.check("k", 5, 60))

    def test_script_is_called_with_prefixed_key(self):
        client = _FakeClient(script_result=1)
        lim = RedisRateLimiter(client, prefix="pfx:")
        lim.check("user:path", 5, 60)
        keys, args = client.script.calls[0]
        self.assertEqual(keys, ["pfx:user:path"])
        self.assertEqual(len(args), 4)  # now, window, limit, member

    def test_falls_back_to_in_process_on_redis_error(self):
        lim = RedisRateLimiter(_FakeClient(script_exc=RuntimeError("redis down")))
        self.assertTrue(lim.check("k", 2, 60))
        self.assertTrue(lim.check("k", 2, 60))
        self.assertFalse(lim.check("k", 2, 60))  # in-process fallback enforced

    def test_falls_back_when_script_registration_unavailable(self):
        lim = RedisRateLimiter(_FakeClient(register_exc=RuntimeError("no EVAL")))
        self.assertIsNone(lim._script)
        self.assertTrue(lim.check("k", 1, 60))


class TestBuildRateLimiter(unittest.TestCase):
    def test_no_redis_url_returns_in_process(self):
        lim = build_rate_limiter("")
        self.assertIsInstance(lim, InProcessRateLimiter)

    def test_redis_package_missing_returns_in_process(self):
        with mock.patch.dict(sys.modules, {"redis": None}):
            lim = build_rate_limiter("redis://localhost:6379/0")
        self.assertIsInstance(lim, InProcessRateLimiter)

    def test_unreachable_redis_returns_in_process(self):
        class _Factory:
            def from_url(self, url, **kwargs):
                raise ConnectionError("refused")

        fake_redis = types.SimpleNamespace(Redis=_Factory())
        with mock.patch.dict(sys.modules, {"redis": fake_redis}):
            lim = build_rate_limiter("redis://localhost:6379/0")
        self.assertIsInstance(lim, InProcessRateLimiter)

    def test_reachable_redis_returns_redis_limiter(self):
        client = _FakeClient()

        class _Factory:
            def from_url(self, url, **kwargs):
                return client

        fake_redis = types.SimpleNamespace(Redis=_Factory())
        with mock.patch.dict(sys.modules, {"redis": fake_redis}):
            lim = build_rate_limiter("redis://localhost:6379/0")
        self.assertIsInstance(lim, RedisRateLimiter)
        self.assertTrue(client.pinged)


if __name__ == "__main__":
    unittest.main()
