"""Distributed-safe request rate limiting.

Two interchangeable implementations behind one tiny interface (``check``):

  * :class:`InProcessRateLimiter` — a thread-safe sliding window with **no
    external dependencies**. Correct for a single process / single replica, and
    used as the automatic fallback everywhere else.
  * :class:`RedisRateLimiter` — a Redis sorted-set sliding window executed as an
    atomic Lua script, so the limit is enforced across **all** engine replicas
    sharing one Redis. This is what multi-replica HA deployments need.

:func:`build_rate_limiter` picks Redis when ``REDIS_URL`` is configured *and* the
optional ``redis`` client is importable and reachable; otherwise it degrades to
the in-process window and logs why.

Design notes (production):
  * Redis is treated as a *hard dependency only when configured*: if it is
    unreachable at call time we fall back to the in-process window instead of
    failing every request. A Redis outage therefore degrades protection from
    cluster-wide to per-replica — it never turns into a full outage.
  * Keys carry the caller identity + path and expire automatically
    (``PEXPIRE``), so an unbounded key space is not possible.
  * The in-process limiter prunes idle buckets so memory cannot grow without
    bound under a high-cardinality key space.
"""

import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SynapseRateLimit")

# Atomic sliding-window check:
#   KEYS[1] = bucket key
#   ARGV[1] = now (epoch seconds)   ARGV[2] = window (seconds)
#   ARGV[3] = limit                 ARGV[4] = unique member id
# Returns 1 if the request is allowed, 0 if the limit is exceeded.
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, math.ceil(window * 1000))
return 1
"""


class InProcessRateLimiter:
    """Thread-safe sliding-window limiter, scoped to this process only."""

    backend = "in-process"

    def __init__(self) -> None:
        self._buckets: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        """Return True if the request is allowed, False if the limit is exceeded."""
        now = time.time()
        window_start = now - window_s
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = []
                self._buckets[key] = bucket
            else:
                # Drop timestamps that have fallen out of the window.
                bucket[:] = [t for t in bucket if t > window_start]
            if len(bucket) >= limit:
                # A non-positive limit can never recover; evict the bucket so a
                # hostile key space cannot grow without bound.
                if limit <= 0:
                    self._buckets.pop(key, None)
                return False
            bucket.append(now)
            return True

    def prune_idle(self, max_idle_seconds: float = 300.0) -> int:
        """Remove buckets whose newest timestamp is older than max_idle_seconds.

        Bounds memory growth under a high-cardinality key space (e.g. spoofed
        API keys). Call periodically from a background thread or request path.
        Returns the number of buckets removed.
        """
        cutoff = time.time() - max_idle_seconds
        removed = 0
        with self._lock:
            stale = [
                key for key, bucket in self._buckets.items()
                if not bucket or bucket[-1] < cutoff
            ]
            for key in stale:
                self._buckets.pop(key, None)
                removed += 1
        return removed

    def reset(self) -> None:
        """Clear all windows (used by tests and on config reload)."""
        with self._lock:
            self._buckets.clear()


class RedisRateLimiter:
    """Redis sorted-set sliding-window limiter shared across replicas."""

    backend = "redis"

    def __init__(
        self,
        client: Any,
        prefix: str = "synapse:rl:",
        fallback: Optional[InProcessRateLimiter] = None,
    ) -> None:
        self._client = client
        self._prefix = prefix
        # Local safety net used when Redis is temporarily unreachable.
        self._fallback = fallback if fallback is not None else InProcessRateLimiter()
        try:
            self._script = client.register_script(_SLIDING_WINDOW_LUA)
        except Exception as exc:  # pragma: no cover - client-specific
            logger.warning("Redis EVAL unavailable (%s); falling back per-call.", exc)
            self._script = None

    def check(self, key: str, limit: int, window_s: float = 60.0) -> bool:
        if self._script is None:
            return self._fallback.check(key, limit, window_s)
        redis_key = f"{self._prefix}{key}"
        member = f"{time.time()}-{uuid.uuid4().hex}"
        try:
            allowed = self._script(
                keys=[redis_key],
                args=[time.time(), window_s, limit, member],
            )
            return bool(int(allowed))
        except Exception as exc:
            logger.warning("Redis rate-limit check failed (%s); using in-process fallback.", exc)
            return self._fallback.check(key, limit, window_s)


def build_rate_limiter(redis_url: Optional[str] = None) -> Any:
    """Construct the best available limiter.

    Prefers a Redis-backed limiter when ``REDIS_URL`` (or the explicit argument)
    is set and Redis is reachable; otherwise returns an
    :class:`InProcessRateLimiter`. Never raises.
    """
    url = redis_url if redis_url is not None else os.environ.get("REDIS_URL", "")
    if not url:
        logger.info("REDIS_URL not set; using in-process rate limiter (single-replica only).")
        return InProcessRateLimiter()
    try:
        import redis  # type: ignore
    except Exception:
        logger.warning(
            "REDIS_URL set but the 'redis' package is not installed; using in-process rate limiter."
        )
        return InProcessRateLimiter()
    try:
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            decode_responses=False,
        )
        client.ping()
        logger.info("Distributed rate limiting enabled via Redis.")
        return RedisRateLimiter(client)
    except Exception as exc:
        logger.warning("Redis unreachable (%s); using in-process rate limiter.", exc)
        return InProcessRateLimiter()
