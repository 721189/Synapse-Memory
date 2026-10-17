"""Smoke test for examples/project_aware_coding_assistant/server.py.

Exercises every route group: projects CRUD, documents, snippets, links,
tenant-isolated memories, and search. Run from the repo root:

    python examples/project_aware_coding_assistant/smoke_test.py
"""
import os
import sys
import tempfile
import uuid

# Deterministic embeddings + isolated temp DBs before importing the server.
os.environ["SYNAPSE_EMBEDDING_PROVIDER"] = "deterministic"
os.environ["PROJECTS_DB"] = os.path.join(tempfile.mkdtemp(), "projects_smoke.db")
os.environ["SYNAPSE_MEMORY_DB"] = os.path.join(tempfile.mkdtemp(), "memory_smoke.db")
os.environ.pop("SYNAPSE_ENCRYPTION_KEY", None)  # dev fallback keyfile path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

client = TestClient(server.app)

def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        sys.exit(1)

# ── Projects CRUD ───────────────────────────────────────────────────────────
slug_a, slug_b = f"proj-a-{uuid.uuid4().hex[:6]}", f"proj-b-{uuid.uuid4().hex[:6]}"
r = client.post("/projects", json={"slug": slug_a, "name": "Project A", "language": "python"})
check("create project A", r.status_code == 201, r.text)
pid_a = r.json()["id"]

r = client.post("/projects", json={"slug": slug_b, "name": "Project B"})
check("create project B", r.status_code == 201, r.text)
pid_b = r.json()["id"]

r = client.post("/projects", json={"slug": slug_a, "name": "Dup"})
check("duplicate slug rejected (409)", r.status_code == 409, r.text)

r = client.post("/projects", json={"slug": "BAD SLUG!", "name": "x"})
check("invalid slug rejected (422)", r.status_code == 422, r.text)

r = client.get("/projects")
check("list projects", r.status_code == 200 and len(r.json()) >= 2, r.text)

r = client.get(f"/projects/{pid_a}")
check("get project A", r.status_code == 200 and r.json()["slug"] == slug_a, r.text)

r = client.put(f"/projects/{pid_a}", json={"slug": slug_a, "name": "Project A v2"})
check("update project A", r.status_code == 200 and r.json()["name"] == "Project A v2", r.text)

r = client.get("/projects/does-not-exist")
check("get missing project 404", r.status_code == 404, r.text)

# ── Documents ───────────────────────────────────────────────────────────────
r = client.post(f"/projects/{pid_a}/documents",
                json={"slug": "readme", "title": "Readme", "content": "# Hello Synapse"})
check("create document", r.status_code == 201, r.text)
did = r.json()["id"]

r = client.post(f"/projects/{pid_a}/documents", json={"slug": "readme", "title": "Dup"})
check("duplicate doc slug 409", r.status_code == 409, r.text)

r = client.get(f"/projects/{pid_a}/documents")
check("list documents", r.status_code == 200 and len(r.json()) == 1, r.text)

r = client.get(f"/projects/{pid_a}/documents/{did}")
check("get document", r.status_code == 200 and r.json()["content"] == "# Hello Synapse", r.text)

r = client.put(f"/projects/{pid_a}/documents/{did}",
               json={"slug": "readme", "title": "Readme v2", "content": "# Updated"})
check("update document", r.status_code == 200 and r.json()["title"] == "Readme v2", r.text)

# Cross-project isolation: document of A not visible under B
r = client.get(f"/projects/{pid_b}/documents/{did}")
check("doc invisible under other project (404)", r.status_code == 404, r.text)


# ── Snippets ────────────────────────────────────────────────────────────────
r = client.post(f"/projects/{pid_a}/snippets",
                json={"file_path": "core/engine.py", "class_name": "Engine",
                      "function_name": "run", "language": "python",
                      "raw_text": "class Engine:\n    def run(self): ...",
                      "summary": "Core engine"})
check("create snippet", r.status_code == 201, r.text)
sid = r.json()["id"]

r = client.post(f"/projects/{pid_a}/snippets",
                json={"raw_text": "x", "document_id": "nope"})
check("snippet with bad doc id 404", r.status_code == 404, r.text)

r = client.get(f"/projects/{pid_a}/snippets/class/Engine")
check("list snippets by class", r.status_code == 200 and len(r.json()) == 1, r.text)

r = client.get(f"/projects/{pid_a}/snippets/function/run")
check("list snippets by function", r.status_code == 200 and len(r.json()) == 1, r.text)

# ── Links ───────────────────────────────────────────────────────────────────
r = client.post("/links", json={"source_project_id": pid_a, "target_project_id": pid_b,
                                "relation": "depends_on", "note": "shares models"})
check("create link", r.status_code == 201, r.text)
lid = r.json()["id"]

r = client.post("/links", json={"source_project_id": pid_a, "target_project_id": "ghost"})
check("link to missing project 404", r.status_code == 404, r.text)

r = client.get("/links")
check("list links", r.status_code == 200 and len(r.json()) == 1, r.text)

# ── Memories (tenant-isolated via Synapse-Memory SDK) ──────────────────────
r = client.post(f"/projects/{pid_a}/memories",
                json={"content": "Project A uses PostgreSQL for storage", "category": "note"})
check("ingest memory A", r.status_code == 201, r.text)
mem_a = r.json()["id"]

r = client.post(f"/projects/{pid_b}/memories",
                json={"content": "Project B prefers SQLite backend", "category": "note"})
check("ingest memory B", r.status_code == 201, r.text)

r = client.get(f"/projects/{pid_a}/memories")
check("list memories A", r.status_code == 200 and len(r.json()) == 1, r.text)
check("memory A tenant isolated", r.json()[0]["tenant_id"] == pid_a, r.text)

r = client.get(f"/projects/{pid_b}/memories")
mems_b = r.json()
check("list memories B", r.status_code == 200 and len(mems_b) == 1, r.text)
check("memory B has no A content",
      all("PostgreSQL" not in m["content"] for m in mems_b), r.text)

# Dedup: same content again should MERGE into existing id, not create new
r = client.post(f"/projects/{pid_a}/memories",
                json={"content": "Project A uses PostgreSQL for storage", "category": "note"})
check("duplicate memory merges", r.status_code == 201 and r.json()["id"] == mem_a, r.text)

# ── Search ──────────────────────────────────────────────────────────────────
r = client.get(f"/search/{pid_a}", params={"q": "Updated"})
types = {x["type"] for x in r.json()}
check("search hits document", r.status_code == 200 and "document" in types, r.text)

r = client.get(f"/search/{pid_a}", params={"q": "engine"})
types = {x["type"] for x in r.json()}
check("search hits snippet", r.status_code == 200 and "snippet" in types, r.text)

r = client.get(f"/search/{pid_a}", params={"q": "PostgreSQL"})
types = {x["type"] for x in r.json()}
check("search hits memory", r.status_code == 200 and "memory" in types, r.text)

r = client.get(f"/search/{pid_b}", params={"q": "PostgreSQL"})
mem_hits = [x for x in r.json() if x["type"] == "memory"]
check("search B does not leak A memory", mem_hits == [], r.text)

# ── Deletes (cascade) ──────────────────────────────────────────────────────
r = client.delete(f"/projects/{pid_a}/documents/{did}")
check("delete document", r.status_code == 204, r.text)

r = client.delete(f"/projects/{pid_a}/snippets/{sid}")
check("delete snippet", r.status_code == 204, r.text)

r = client.delete(f"/links/{lid}")
check("delete link", r.status_code == 204, r.text)

r = client.delete(f"/projects/{pid_a}")
check("delete project A", r.status_code == 204, r.text)

r = client.get(f"/projects/{pid_a}/documents")
check("documents cascade-deleted", r.status_code == 200 and r.json() == [], r.text)

r = client.delete(f"/projects/{pid_b}")
check("delete project B", r.status_code == 204, r.text)

print("\nAll smoke tests passed.")

