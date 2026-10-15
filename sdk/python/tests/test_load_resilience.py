"""Phase 7.3 load & resilience suite.

Offline-by-design coverage of the five production scenarios tracked in
docs/production-hardening-baseline.md (checklist item 9):

1. Concurrency ......... parallel writers/readers against the store
2. DB interruption ..... storage outage must raise (fail-fast), never fake
                         success, and recover without corruption
3. Slow provider ....... a slow embedding provider degrades latency but not
                         correctness; cache bounds repeated embed work
4. Worker/DB restart ... crash-style reopen preserves every row; schema
                         migrations stay idempotent across restarts
5. Large batches ....... 1000-row corpus + 100-item bulk API: search quality,
                         tenant isolation, and auto-prune bounds hold

The live-PostgreSQL interruption path is covered by CI's
tests/test_pgvector_integration.py
(TestIntegrationFailureModes::test_failure_mode_postgres_unavailable);
this suite exercises the SQLite single-node topology locally with zero
external dependencies (no Docker, no psql, no network).
"""
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from typing import Any, Callable, Dict, List
from unittest import mock

from fastapi.testclient import TestClient

import synapse_memory.api.fastapi_server as server
from synapse_memory.api.fastapi_server import app, security_manager
from synapse_memory.core.embedding_provider import (
    DeterministicHashEmbeddingProvider,
    EmbeddingManager,
)
from synapse_memory.core.memory_manager import MemoryManager
from synapse_memory.core.sqlite_db import SQLiteMemoryStore

DIM = 8


def _embedding(idx: int) -> List[float]:
    """Deterministic, mostly-distinct float vector for row `idx`."""
    vec = [float(idx % 13) + 0.1]
    vec.extend(
        float((idx * (k + 1)) % 17) * 0.25 + 0.05 for k in range(DIM - 1)
    )
    return vec


def _make_memory(
    idx: int, tenant: str = "default", category: str = "interaction"
) -> Dict[str, Any]:
    """Structured memory row with a globally-unique lexical marker token."""
    return {
        "id": f"load_{idx}",
        "tenant_id": tenant,
        "content": f"load resilience memory {idx} zqltok{idx}",
        "category": category,
        "confidence": 0.9,
        "created_at": time.time(),
        "access_count": 0,
        "token_cost": 10,
        "embedding": _embedding(idx),
        "embedding_dimension": DIM,
        "model_version": "load-suite",
    }


def _integrity_ok(db_path: str) -> bool:
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and str(row[0]).lower() == "ok"
    finally:
        conn.close()


def _schema_snapshot(db_path: str) -> List[Any]:
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        return sorted(conn.execute("SELECT type, name FROM sqlite_master"))
    finally:
        conn.close()


def _run_parallel(workers: List[Callable[[], None]]) -> List[str]:
    """Run callables in threads; return one message per failure/hang."""
    errors: List[str] = []
    lock = threading.Lock()

    def _runner(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # collected for assertion by the test thread
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=_runner, args=(fn,)) for fn in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
        if t.is_alive():
            with lock:
                errors.append("worker thread did not finish within 90s")
    return errors


class _TempStoreTestCase(unittest.TestCase):
    """Base: file-backed SQLite store in a temp dir (restart/concurrency safe).

    Uses a FILE-based store rather than ':memory:' because file + WAL is the
    real concurrency and crash-restart surface (per-query connections, WAL
    sidecars); ':memory:' is a single shared connection by design.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="synapse_load_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.db_path = os.path.join(self.tmpdir, "load.db")
        self.store = SQLiteMemoryStore(self.db_path)
        # addCleanup runs LIFO: close (checkpoint WAL) before rmtree.
        self.addCleanup(self.store.close)


class TestConcurrencyResilience(_TempStoreTestCase):
    """Scenario 1: parallel writers and readers must lose nothing."""

    def test_parallel_inserts_lose_no_rows(self) -> None:
        per_thread, threads = 25, 8

        def make_worker(start: int) -> Callable[[], None]:
            def worker() -> None:
                for idx in range(start, start + per_thread):
                    self.store.insert_memory(_make_memory(idx))
            return worker

        errors = _run_parallel(
            [make_worker(t * per_thread) for t in range(threads)]
        )
        self.assertEqual(errors, [], f"parallel writers failed: {errors}")
        self.assertEqual(self.store.count_memories(), per_thread * threads)
        sample = self.store.get_memory_by_id("load_137")
        self.assertIsNotNone(sample)
        self.assertEqual(
            sample["content"], "load resilience memory 137 zqltok137"
        )

    def test_parallel_access_counter_updates_are_atomic(self) -> None:
        self.store.insert_memory(_make_memory(1))
        per_thread = 10

        def make_worker() -> Callable[[], None]:
            def worker() -> None:
                for _ in range(per_thread):
                    self.store.update_access_count("load_1", 1)
            return worker

        errors = _run_parallel([make_worker() for _ in range(10)])
        self.assertEqual(errors, [], f"counter writers failed: {errors}")
        final = self.store.get_memory_by_id("load_1")
        self.assertIsNotNone(final)
        # SQL-side `access_count = access_count + ?` must serialize: no lost
        # increments under 10 concurrent read-modify-write threads.
        self.assertEqual(final["access_count"], per_thread * 10)

    def test_concurrent_readers_never_observe_torn_rows(self) -> None:
        rows = 40

        def writer() -> None:
            for idx in range(rows):
                self.store.insert_memory(_make_memory(idx))
                time.sleep(0.002)

        def make_reader() -> Callable[[], None]:
            def reader() -> None:
                for _ in range(40):
                    # Decrypting a half-written row would raise
                    # (InvalidToken / JSON error); a clean read never does.
                    self.store.get_all_memories()
            return reader

        errors = _run_parallel([writer, make_reader(), make_reader()])
        self.assertEqual(errors, [], f"torn reads detected: {errors}")
        self.assertEqual(self.store.count_memories(), rows)


class TestDbInterruptionResilience(_TempStoreTestCase):
    """Scenario 2: an interrupted database must fail fast and recover."""

    def test_outage_raises_and_state_stays_intact(self) -> None:
        for i in range(5):
            self.store.insert_memory(_make_memory(i))
        self.assertEqual(self.store.count_memories(), 5)

        with mock.patch.object(
            self.store,
            "_get_connection",
            side_effect=sqlite3.OperationalError(
                "simulated outage: database is locked"
            ),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.insert_memory(_make_memory(99))
            with self.assertRaises(sqlite3.OperationalError):
                self.store.get_all_memories()
            with self.assertRaises(sqlite3.OperationalError):
                self.store.vector_search([0.1] * DIM)

        # The outage left no partial rows behind ...
        self.assertEqual(self.store.count_memories(), 5)
        # ... and the store is fully functional once storage returns.
        self.store.insert_memory(_make_memory(6))
        self.assertEqual(self.store.count_memories(), 6)

    def test_startup_fails_fast_when_storage_unusable(self) -> None:
        # Parent directory does not exist: bootstrap must raise instead of
        # silently degrading to a degraded/in-memory state.
        bad_path = os.path.join(self.tmpdir, "missing_dir", "nested.db")
        with self.assertRaises(
            (sqlite3.OperationalError, OSError, RuntimeError)
        ):
            SQLiteMemoryStore(bad_path, fail_closed=True)


class _SlowProvider(DeterministicHashEmbeddingProvider):
    """Deterministic provider with an injectable per-embed latency."""

    def __init__(self, dim: int = DIM, delay_s: float = 0.02) -> None:
        super().__init__(dim=dim)
        self.delay_s = delay_s
        self.calls = 0
        self.lock = threading.Lock()

    def embed(self, text: str) -> List[float]:
        with self.lock:
            self.calls += 1
        time.sleep(self.delay_s)
        return super().embed(text)


class _ShimEmbedder:
    """Minimal SynapseEmbedder-shaped facade (embed_query + provenance)."""

    def __init__(self, provider: _SlowProvider) -> None:
        self.manager = EmbeddingManager(provider)
        self.provider = provider
        self.model_name = "slow-load-test-provider"

    @property
    def dimension(self) -> int:
        return self.provider.dimension

    def embed_query(self, text: str) -> List[float]:
        return self.manager.get_embedding(text)


class TestSlowProviderResilience(_TempStoreTestCase):
    """Scenario 3: a slow embedding provider must degrade, not corrupt."""

    def test_ingest_and_search_under_slow_provider(self) -> None:
        provider = _SlowProvider(delay_s=0.02)
        embedder = _ShimEmbedder(provider)
        manager = MemoryManager(store=self.store, embedder=embedder)  # type: ignore[arg-type]

        start = time.perf_counter()
        for i in range(10):
            memory_id, action, _cost = manager.ingest_with_deduplication(
                content=f"slow provider ingest {i} zqlslow{i}",
                category="interaction",
                tenant_id="tenant_slow",
            )
            self.assertTrue(memory_id.startswith("mem_"))
            self.assertIn(action, ("CREATED", "MERGED"))
        ingest_s = time.perf_counter() - start

        query_vec = embedder.embed_query("slow provider probe zqlslow4")
        results = self.store.vector_search(
            query_vec, top_k=5, tenant_id="tenant_slow"
        )
        # Dedup may MERGE near-identical slow ingests, so the tenant can hold
        # fewer than 10 rows; retrieval must still return valid scoped hits.
        self.assertGreaterEqual(len(results), 1)
        self.assertLessEqual(len(results), 5)
        self.assertTrue(
            all(r["tenant_id"] == "tenant_slow" for r in results)
        )
        # Exactly one embed per ingest + one per query: a slow provider must
        # never trigger hidden retry/re-embed storms.
        self.assertEqual(provider.calls, 11)
        # Serial latency is honored (10 x 20ms floor) and bounded above:
        # no queueing blow-up beyond the injected delay.
        self.assertGreaterEqual(ingest_s, 0.15)
        self.assertLess(ingest_s, 15.0)

    def test_embedding_cache_shields_repeated_queries(self) -> None:
        provider = _SlowProvider(delay_s=0.01)
        manager = EmbeddingManager(provider)
        text = f"repeat shield probe {uuid.uuid4().hex}"

        first = manager.get_embedding(text)
        second = manager.get_embedding(text)

        self.assertEqual(first, second)
        # lru_cache on get_embedding: repeated identical queries hit the
        # cache instead of re-paying the slow provider.
        self.assertEqual(provider.calls, 1)


class TestRestartDurability(_TempStoreTestCase):
    """Scenario 4: worker/DB restarts must never lose or corrupt data."""

    def test_clean_restart_preserves_every_row(self) -> None:
        for i in range(25):
            self.store.insert_memory(_make_memory(i))
        self.store.close()

        reopened = SQLiteMemoryStore(self.db_path)
        self.addCleanup(reopened.close)

        after = reopened.get_all_memories()
        self.assertEqual(len(after), 25)
        by_id = {m["id"]: m for m in after}
        for i in (0, 7, 24):
            self.assertEqual(
                by_id[f"load_{i}"]["content"],
                f"load resilience memory {i} zqltok{i}",
            )
            self.assertEqual(by_id[f"load_{i}"]["embedding"], _embedding(i))
        self.assertTrue(_integrity_ok(self.db_path))

    def test_crash_restart_with_stale_wal_sidecars_recovers(self) -> None:
        for i in range(15):
            self.store.insert_memory(_make_memory(i))
        # Simulate a crash: never call close() (no WAL checkpoint) and open
        # a second handle, as a replacement worker process would after the
        # old process was killed. The reopened store must read through the
        # stale -wal/-shm sidecars without loss or corruption.
        crashed = SQLiteMemoryStore(self.db_path)
        self.assertIsNotNone(crashed.count_memories())

        reopened = SQLiteMemoryStore(self.db_path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.count_memories(), 15)
        self.assertTrue(_integrity_ok(self.db_path))

    def test_schema_is_idempotent_across_restarts(self) -> None:
        schemas: List[List[Any]] = []
        for round_no in range(3):
            store = SQLiteMemoryStore(self.db_path)
            store.insert_memory(_make_memory(round_no))
            self.assertEqual(store.count_memories(), round_no + 1)
            store.close()
            schemas.append(_schema_snapshot(self.db_path))

        # No table/index drift between restarts: migrations re-run cleanly.
        self.assertEqual(schemas[0], schemas[1])
        self.assertEqual(schemas[1], schemas[2])
        self.assertTrue(_integrity_ok(self.db_path))


class TestLargeBatchThroughput(_TempStoreTestCase):
    """Scenario 5: batch-scale writes must stay correct, fast, and isolated."""

    def test_thousand_row_corpus_round_trips(self) -> None:
        rows = 1000
        start = time.perf_counter()
        for i in range(rows):
            self.store.insert_memory(_make_memory(i))
        write_s = time.perf_counter() - start

        self.assertEqual(self.store.count_memories(), rows)

        hits = self.store.vector_search(_embedding(500), top_k=10)
        self.assertEqual(len(hits), 10)
        cosines = [h["cosine_similarity"] for h in hits]
        self.assertEqual(cosines, sorted(cosines, reverse=True))
        self.assertIn("load_500", [h["id"] for h in hits])

        lexical = self.store.lexical_search("zqltok737", top_k=5)
        self.assertGreater(len(lexical), 0)
        self.assertEqual(lexical[0]["id"], "load_737")

        # Generous ceiling: guards against pathological O(N^2) regressions
        # while staying stable on slow CI runners.
        self.assertLess(write_s, 90.0)

    def test_tenant_and_category_isolation_hold_at_batch_size(self) -> None:
        for i in range(200):
            category = "fact" if i % 2 == 0 else "preference"
            self.store.insert_memory(
                _make_memory(i, tenant="tenant_batch_a", category=category)
            )
            self.store.insert_memory(
                _make_memory(
                    1000 + i, tenant="tenant_batch_b", category=category
                )
            )

        self.assertEqual(self.store.count_memories("tenant_batch_a"), 200)
        self.assertEqual(self.store.count_memories("tenant_batch_b"), 200)
        self.assertEqual(
            len(self.store.get_memories_by_category(
                "fact", tenant_id="tenant_batch_a"
            )),
            100,
        )
        self.assertEqual(
            len(self.store.get_memories_by_category(
                "preference", tenant_id="tenant_batch_b"
            )),
            100,
        )
        # Cross-tenant retrieval stays isolated even at batch scale.
        hits = self.store.vector_search(
            _embedding(1000), top_k=10, tenant_id="tenant_batch_a"
        )
        self.assertTrue(hits)
        self.assertTrue(
            all(h["tenant_id"] == "tenant_batch_a" for h in hits)
        )


class TestHttpLoadOutageAndSlowProvider(unittest.TestCase):
    """Scenario 5 (HTTP): load bursts, bulk batches, slow provider, outage.

    Exercises the real FastAPI gateway (auth, rate limiting, metrics,
    dedup, auto-prune) with dedicated tenants so parallel test modules
    sharing the gateway SQLite file are unaffected.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
        cls.write_headers = cls._headers(
            "Load Write Key", "tenant_load_write",
            ["memories:read", "memories:write"],
        )
        cls.bulk_headers = cls._headers(
            "Load Bulk Key", "tenant_load_bulk",
            ["memories:read", "memories:write"],
        )
        cls.read_headers = cls._headers(
            "Load Read Key", "tenant_load_read", ["memories:read"],
        )

    @classmethod
    def _headers(
        cls, name: str, tenant: str, scopes: List[str]
        ) -> Dict[str, str]:
        token, _record = security_manager.generate_api_key(
            name=name,
            organization_id="org_load",
            project_id="proj_load",
            tenant_id=tenant,
            scopes=scopes,
            role="developer",
        )
        return {"X-Synapse-API-Key": token, "X-Tenant-ID": tenant}

    @classmethod
    def tearDownClass(cls) -> None:
        for tenant in (
            "tenant_load_write", "tenant_load_bulk", "tenant_load_read"
        ):
            try:
                server.store.clear_memories(tenant_id=tenant)
            except Exception:
                pass

    def test_read_burst_all_success(self) -> None:
        start = time.perf_counter()
        for _ in range(50):
            r = self.client.get("/api/memories", headers=self.read_headers)
            self.assertEqual(r.status_code, 200)
        self.assertLess(time.perf_counter() - start, 30.0)

    def test_write_burst_ingests_all_success(self) -> None:
        # Earlier tests in this class may already hold rows in this tenant;
        # measure the delta this burst contributes.
        baseline = server.store.count_memories("tenant_load_write")
        start = time.perf_counter()
        for i in range(30):
            r = self.client.post(
                "/api/memories/add",
                json={"content": f"http burst memory {i} zqlburst{i}"},
                headers=self.write_headers,
            )
            self.assertEqual(r.status_code, 201)
        burst_s = time.perf_counter() - start

        added = server.store.count_memories("tenant_load_write") - baseline
        # Dedup may merge some near-identical burst texts: the burst must add
        # at least one row and never more than one per request.
        self.assertGreater(added, 0)
        self.assertLessEqual(added, 30)
        self.assertLess(burst_s, 60.0)

    def test_bulk_batch_ingests_and_engages_auto_prune(self) -> None:
        items = [
            {"content": f"bulk backfill item {uuid.uuid4().hex} zqlbulk{i}"}
            for i in range(100)
        ]
        start = time.perf_counter()
        r = self.client.post(
            "/api/memories/bulk",
            json={"items": items},
            headers=self.bulk_headers,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["success"])
        # Every item is processed (CREATED or dedup-merged), none dropped.
        self.assertEqual(body["data"]["ingested"], 100)

        # The manager's CapacityPruningPolicy keeps each tenant bounded at
        # max_record_limit (50): sustained batches must not grow unbounded.
        rows = server.store.count_memories("tenant_load_bulk")
        self.assertGreaterEqual(rows, 1)
        self.assertLessEqual(rows, 50)
        self.assertLess(time.perf_counter() - start, 120.0)

    def test_slow_provider_degrades_latency_not_success(self) -> None:
        delay = 0.5
        original = server.embedder.embed_query

        def _slow_embed(text: str) -> List[float]:
            time.sleep(delay)
            return original(text)

        # The gateway shares ONE SynapseEmbedder between /ingest (via
        # manager.embedder) and /query (module global): patching the
        # instance covers both paths.
        with mock.patch.object(
            server.embedder, "embed_query", side_effect=_slow_embed
        ):
            t0 = time.perf_counter()
            ingest = self.client.post(
                "/api/memories/add",
                json={"content": f"slow http ingest {uuid.uuid4().hex}"},
                headers=self.write_headers,
            )
            ingest_s = time.perf_counter() - t0
            self.assertEqual(ingest.status_code, 201)
            self.assertGreaterEqual(ingest_s, 0.35)
            self.assertLess(ingest_s, 10.0)

            t0 = time.perf_counter()
            query = self.client.post(
                "/query",
                json={"prompt": f"slow http probe {uuid.uuid4().hex}"},
                headers=self.read_headers,
            )
            query_s = time.perf_counter() - t0
            self.assertEqual(query.status_code, 200)
            self.assertGreaterEqual(query_s, 0.35)
            self.assertLess(query_s, 10.0)

    def test_db_outage_fails_closed_then_recovers(self) -> None:
        strict = TestClient(app, raise_server_exceptions=False)
        errors_before = server._ingest_errors

        with mock.patch.object(
            server.store,
            "_get_connection",
            side_effect=sqlite3.OperationalError(
                "simulated outage: database is locked"
            ),
        ):
            # Ingest during the outage must FAIL (500), never fake a 2xx.
            bad = strict.post(
                "/api/memories/add",
                json={"content": "must not be ingested during outage"},
                headers=self.write_headers,
            )
            self.assertEqual(bad.status_code, 500)
            # The failure was recorded, not silently swallowed.
            self.assertGreater(server._ingest_errors, errors_before)

            # /health must not claim healthy while storage is down ...
            health = strict.get("/health")
            self.assertNotEqual(health.status_code, 200)
            # ... while /liveness stays DB-independent by contract.
            live = strict.get("/liveness")
            self.assertEqual(live.status_code, 200)

        # Storage returns: the very next request succeeds end-to-end.
        ok = self.client.post(
            "/api/memories/add",
            json={"content": f"post outage ingest {uuid.uuid4().hex}"},
            headers=self.write_headers,
        )
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(self.client.get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
