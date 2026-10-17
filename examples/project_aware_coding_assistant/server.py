#!/usr/bin/env python3
"""
Project-Aware Coding Assistant

A FastAPI application that provides project-scoped context management for
coding agents. Each project is a Synapse-Memory *tenant*, so the SDK's
multi-tenant isolation guarantees apply to notes, snippets, and decisions.

Storage layout:
  1. Application tables (projects, documents, snippets, links) live in a
     plain SQLite database managed by this app (PROJECTS_DB env var).
  2. Semantic memories (notes, decisions, Q&A) are stored via Synapse-Memory
     ``MemoryManager.ingest_with_deduplication`` with ``tenant_id=project.id``
     so every project's memory is isolated from every other project.

Run:
    SYNAPSE_EMBEDDING_PROVIDER=deterministic
    SYNAPSE_ENCRYPTION_KEY=<urlsafe-base64 32-byte key>
    python server.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from synapse_memory.core.sqlite_db import SQLiteMemoryStore
from synapse_memory.core.embedder import SynapseEmbedder
from synapse_memory.core.memory_manager import MemoryManager

# ── Application-table schemas (plain SQLite, NOT the SDK memory table) ──────
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    language    TEXT DEFAULT 'unknown',
    root_path   TEXT DEFAULT '',
    status      TEXT DEFAULT 'active',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    slug       TEXT NOT NULL,
    title      TEXT DEFAULT '',
    content    TEXT DEFAULT '',
    mime_type  TEXT DEFAULT 'text/markdown',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(project_id, slug)
);

CREATE TABLE IF NOT EXISTS snippets (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id   TEXT REFERENCES documents(id) ON DELETE SET NULL,
    file_path     TEXT DEFAULT '',
    class_name    TEXT DEFAULT '',
    function_name TEXT DEFAULT '',
    language      TEXT DEFAULT 'unknown',
    raw_text      TEXT NOT NULL,
    summary       TEXT DEFAULT '',
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS links (
    id                TEXT PRIMARY KEY,
    source_project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    relation          TEXT DEFAULT 'depends_on',
    note              TEXT DEFAULT '',
    created_at        REAL NOT NULL
);
"""

# ── Module-level state ──────────────────────────────────────────────────────
APP_DB = os.environ.get("PROJECTS_DB", "projects.db")
Path(APP_DB).parent.mkdir(parents=True, exist_ok=True)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(APP_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    conn = _db()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Pydantic models ─────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    language: str = "unknown"
    root_path: str = ""
    status: str = "active"

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", v):
            raise ValueError("slug must be lowercase alphanumeric with - or _")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in ("active", "archived"):
            raise ValueError("status must be 'active' or 'archived'")
        return v


class ProjectOut(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    language: str
    root_path: str
    status: str
    created_at: float
    updated_at: float


class DocumentCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=128)
    title: str = ""
    content: str = ""
    mime_type: str = "text/markdown"


class DocumentOut(BaseModel):
    id: str
    project_id: str
    slug: str
    title: str
    content: str
    mime_type: str
    created_at: float
    updated_at: float


class SnippetCreate(BaseModel):
    file_path: str = ""
    class_name: str = ""
    function_name: str = ""
    language: str = "unknown"
    raw_text: str = Field(..., min_length=1)
    summary: str = ""
    document_id: Optional[str] = None


class SnippetOut(BaseModel):
    id: str
    project_id: str
    document_id: Optional[str]
    file_path: str
    class_name: str
    function_name: str
    language: str
    raw_text: str
    summary: str
    created_at: float


class LinkCreate(BaseModel):
    source_project_id: str
    target_project_id: str
    relation: str = "depends_on"
    note: str = ""


class MemoryIngest(BaseModel):
    content: str = Field(..., min_length=1)
    category: str = "note"

# Synapse-Memory integration (semantic memories, tenant-isolated per project)
synapse_store = SQLiteMemoryStore(
    db_path=os.environ.get("SYNAPSE_MEMORY_DB", ":memory:"),
    fail_closed=True,
)
synapse_embedder = SynapseEmbedder(
    provider=os.environ.get("SYNAPSE_EMBEDDING_PROVIDER", "deterministic")
)
manager = MemoryManager(store=synapse_store, embedder=synapse_embedder)

app = FastAPI(title="Project-Aware Coding Assistant")


# ── Project routes ──────────────────────────────────────────────────────────


@app.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate) -> Dict[str, Any]:
    pid = str(uuid.uuid4())
    now = time.time()
    with _tx() as db:
        try:
            db.execute(
                "INSERT INTO projects (id, slug, name, description, language,"
                " root_path, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, payload.slug, payload.name, payload.description,
                 payload.language, payload.root_path, payload.status, now, now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Project slug '{payload.slug}' already exists")
        row = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return dict(row)


@app.get("/projects", response_model=List[ProjectOut])
async def list_projects(status: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    with _tx() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/projects/{pid}", response_model=ProjectOut)
async def get_project(pid: str) -> Dict[str, Any]:
    with _tx() as db:
        row = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "Project not found")
    return dict(row)


@app.put("/projects/{pid}", response_model=ProjectOut)
async def update_project(pid: str, payload: ProjectCreate) -> Dict[str, Any]:
    now = time.time()
    with _tx() as db:
        try:
            cur = db.execute(
                "UPDATE projects SET slug=?, name=?, description=?, language=?,"
                " root_path=?, status=?, updated_at=? WHERE id=?",
                (payload.slug, payload.name, payload.description, payload.language,
                 payload.root_path, payload.status, now, pid),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Project slug '{payload.slug}' already exists")
        if cur.rowcount == 0:
            raise HTTPException(404, "Project not found")
        row = db.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return dict(row)


# ── Document routes ─────────────────────────────────────────────────────────


@app.post("/projects/{pid}/documents", response_model=DocumentOut, status_code=201)
async def create_document(pid: str, payload: DocumentCreate) -> Dict[str, Any]:
    did = str(uuid.uuid4())
    now = time.time()
    with _tx() as db:
        if not db.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Project not found")
        try:
            db.execute(
                "INSERT INTO documents (id, project_id, slug, title, content,"
                " mime_type, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (did, pid, payload.slug, payload.title, payload.content,
                 payload.mime_type, now, now),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Document slug '{payload.slug}' exists in this project")
        row = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    return dict(row)


@app.get("/projects/{pid}/documents", response_model=List[DocumentOut])
async def list_documents(pid: str) -> List[Dict[str, Any]]:
    with _tx() as db:
        rows = db.execute(
            "SELECT * FROM documents WHERE project_id = ? ORDER BY updated_at DESC", (pid,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/projects/{pid}/documents/{did}", response_model=DocumentOut)
async def get_document(pid: str, did: str) -> Dict[str, Any]:
    with _tx() as db:
        row = db.execute(
            "SELECT * FROM documents WHERE id = ? AND project_id = ?", (did, pid)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    return dict(row)


@app.put("/projects/{pid}/documents/{did}", response_model=DocumentOut)
async def update_document(pid: str, did: str, payload: DocumentCreate) -> Dict[str, Any]:
    now = time.time()
    with _tx() as db:
        try:
            cur = db.execute(
                "UPDATE documents SET slug=?, title=?, content=?, mime_type=?,"
                " updated_at=? WHERE id=? AND project_id=?",
                (payload.slug, payload.title, payload.content, payload.mime_type,
                 now, did, pid),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Document slug '{payload.slug}' exists in this project")
        if cur.rowcount == 0:
            raise HTTPException(404, "Document not found")
        row = db.execute("SELECT * FROM documents WHERE id = ?", (did,)).fetchone()
    return dict(row)


@app.delete("/projects/{pid}/documents/{did}", status_code=204)
async def delete_document(pid: str, did: str) -> None:
    with _tx() as db:
        cur = db.execute(
            "DELETE FROM documents WHERE id = ? AND project_id = ?", (did, pid)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Document not found")


@app.delete("/projects/{pid}", status_code=204)
async def delete_project(pid: str) -> None:
    """Delete a project; documents/snippets/links cascade via FK ON DELETE."""
    with _tx() as db:
        cur = db.execute("DELETE FROM projects WHERE id = ?", (pid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Project not found")




# ── Snippet routes ──────────────────────────────────────────────────────────


@app.post("/projects/{pid}/snippets", response_model=SnippetOut, status_code=201)
async def create_snippet(pid: str, payload: SnippetCreate) -> Dict[str, Any]:
    sid = str(uuid.uuid4())
    now = time.time()
    with _tx() as db:
        if not db.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Project not found")
        if payload.document_id is not None:
            if not db.execute(
                "SELECT id FROM documents WHERE id = ? AND project_id = ?",
                (payload.document_id, pid),
            ).fetchone():
                raise HTTPException(404, "Document not found in this project")
        db.execute(
            "INSERT INTO snippets (id, project_id, document_id, file_path,"
            " class_name, function_name, language, raw_text, summary, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, pid, payload.document_id, payload.file_path, payload.class_name,
             payload.function_name, payload.language, payload.raw_text,
             payload.summary, now),
        )
        row = db.execute("SELECT * FROM snippets WHERE id = ?", (sid,)).fetchone()
    return dict(row)


@app.get("/projects/{pid}/snippets", response_model=List[SnippetOut])
async def list_snippets(pid: str) -> List[Dict[str, Any]]:
    with _tx() as db:
        rows = db.execute(
            "SELECT * FROM snippets WHERE project_id = ? ORDER BY created_at DESC", (pid,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/projects/{pid}/snippets/class/{cls}", response_model=List[SnippetOut])
async def list_class_snippets(pid: str, cls: str) -> List[Dict[str, Any]]:
    with _tx() as db:
        rows = db.execute(
            "SELECT * FROM snippets WHERE project_id = ? AND class_name = ?"
            " ORDER BY created_at DESC",
            (pid, cls),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/projects/{pid}/snippets/function/{fn}", response_model=List[SnippetOut])
async def list_function_snippets(pid: str, fn: str) -> List[Dict[str, Any]]:
    with _tx() as db:
        rows = db.execute(
            "SELECT * FROM snippets WHERE project_id = ? AND function_name = ?"
            " ORDER BY created_at DESC",
            (pid, fn),
        ).fetchall()
    return [dict(r) for r in rows]


@app.delete("/projects/{pid}/snippets/{sid}", status_code=204)
async def delete_snippet(pid: str, sid: str) -> None:
    with _tx() as db:
        cur = db.execute("DELETE FROM snippets WHERE id = ? AND project_id = ?", (sid, pid))
        if cur.rowcount == 0:
            raise HTTPException(404, "Snippet not found")

class MemoryOut(BaseModel):
    id: str
    content: str
    category: str
    tenant_id: str
    created_at: float


# ── Cross-project links (global routes, as documented in README) ────────────


@app.post("/links", status_code=201)
async def create_link(payload: LinkCreate) -> Dict[str, str]:
    lid = str(uuid.uuid4())
    now = time.time()
    with _tx() as db:
        for proj in (payload.source_project_id, payload.target_project_id):
            if not db.execute("SELECT id FROM projects WHERE id = ?", (proj,)).fetchone():
                raise HTTPException(404, f"Project '{proj}' not found")
        db.execute(
            "INSERT INTO links (id, source_project_id, target_project_id,"
            " relation, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (lid, payload.source_project_id, payload.target_project_id,
             payload.relation, payload.note, now),
        )
    return {"id": lid, "status": "created"}


@app.get("/links")
async def list_links() -> List[Dict[str, Any]]:
    with _tx() as db:
        rows = db.execute("SELECT * FROM links ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.delete("/links/{lid}", status_code=204)
async def delete_link(lid: str) -> None:
    with _tx() as db:
        cur = db.execute("DELETE FROM links WHERE id = ?", (lid,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Link not found")


# ── Semantic memory routes (Synapse-Memory SDK, tenant = project id) ───────


@app.post("/projects/{pid}/memories", response_model=MemoryOut, status_code=201)
async def ingest_memory(pid: str, payload: MemoryIngest) -> Dict[str, Any]:
    """Ingest a semantic memory scoped to project *pid* via Synapse-Memory.

    Every memory is stored with ``tenant_id=pid`` so the SDK's isolation
    guarantees apply — no other project's memories can leak in or out.
    """
    with _tx() as db:
        if not db.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Project not found")

    mid, status, cost = manager.ingest_with_deduplication(
        content=payload.content,
        category=payload.category,
        tenant_id=pid,
    )
    if mid == "REJECTED":
        raise HTTPException(422, "Memory rejected by deduplication policy")

    mem = synapse_store.get_memory_by_id(mid, tenant_id=pid)
    if mem is None:
        raise HTTPException(500, "Memory ingested but could not be read back")
    return {
        "id": mid,
        "content": mem.get("content", payload.content),
        "category": mem.get("category", payload.category),
        "tenant_id": mem.get("tenant_id", pid),
        "created_at": mem.get("created_at", time.time()),
    }


@app.get("/projects/{pid}/memories", response_model=List[MemoryOut])
async def list_memories(pid: str) -> List[Dict[str, Any]]:
    """List all semantic memories scoped to project *pid*."""
    with _tx() as db:
        if not db.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Project not found")
    memories = synapse_store.get_all_memories(tenant_id=pid)
    return [
        {
            "id": m.get("id", ""),
            "content": m.get("content", ""),
            "category": m.get("category", ""),
            "tenant_id": m.get("tenant_id", pid),
            "created_at": m.get("created_at", 0.0),
        }
        for m in memories
    ]


# ── Search (documents + snippets + memories within one project) ────────────


@app.get("/search/{pid}")
async def search_project(
    pid: str,
    q: str = Query(..., min_length=1),
    top_k: int = Query(default=10, ge=1, le=50),
) -> List[Dict[str, Any]]:
    """Search documents, snippets, and memories within a single project."""
    results: List[Dict[str, Any]] = []
    like = f"%{q}%"

    with _tx() as db:
        if not db.execute("SELECT id FROM projects WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Project not found")

        for row in db.execute(
            "SELECT id, slug, title, content FROM documents"
            " WHERE project_id = ? AND (content LIKE ? OR title LIKE ?) LIMIT ?",
            (pid, like, like, top_k),
        ):
            results.append({
                "type": "document", "id": row["id"], "slug": row["slug"],
                "title": row["title"], "preview": (row["content"] or "")[:200],
            })

        for row in db.execute(
            "SELECT id, class_name, function_name, raw_text, summary FROM snippets"
            " WHERE project_id = ? AND (raw_text LIKE ? OR summary LIKE ?) LIMIT ?",
            (pid, like, like, top_k),
        ):
            results.append({
                "type": "snippet", "id": row["id"],
                "class_name": row["class_name"], "function_name": row["function_name"],
                "summary": row["summary"], "preview": (row["raw_text"] or "")[:200],
            })

    # Semantic memories (tenant-isolated)
    memories = synapse_store.get_all_memories(tenant_id=pid)
    q_lower = q.lower()
    for m in memories:
        content = m.get("content", "")
        if q_lower in content.lower():
            results.append({
                "type": "memory", "id": m.get("id", ""),
                "category": m.get("category", ""),
                "preview": content[:200],
            })

    return results[:top_k]


# ── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
