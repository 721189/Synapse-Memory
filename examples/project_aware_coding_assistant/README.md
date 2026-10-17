# Project-Aware Coding Assistant

A FastAPI application providing project-scoped context management for coding
agents. It stores projects, documents, code snippets, and cross-project links,
with semantic search across all artifacts.

## Architecture

```
projects/
  ├── id (UUID)
  ├── slug (unique)
  ├── name, description, language
  ├── root_path (filesystem path)
  └── status: active | archived
├── documents/
│   ├── id (UUID)
│   ├── project_id (FK → projects)
│   ├── slug, title
│   ├── content (markdown)
│   └── mime_type
├── snippets/
│   ├── id (UUID)
│   ├── project_id (FK → projects)
│   ├── document_id (FK → documents)
│   ├── file_path
│   ├── class_name
│   ├── function_name
│   └── raw_text, summary, language
└── links/ (cross-project relations)
    ├── source_project_id (FK)
    ├── target_project_id (FK)
    └── relation, note
```

## Setup

```bash
cd examples/project_aware_coding_assistant
python -m venv .venv && source .venv/bin/activate
pip install -e "sdk/python[dev]"

# Set environment variables
export SYNAPSE_PROJECTS_DB=./data/projects.db
export SYNAPSE_ENCRYPTION_KEY=your-secret-key-32-bytes-here
export PORT=8000
```

## Run

```bash
uvicorn server:app --reload --port $PORT
```

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/projects` | Create a project |
| GET | `/projects` | List projects (filter by status) |
| GET | `/projects/{id}` | Get a project |
| PUT | `/projects/{id}` | Update a project |
| DELETE | `/projects/{id}` | Delete a project |
| POST | `/projects/{pid}/documents` | Create a document |
| GET | `/projects/{pid}/documents` | List documents |
| GET | `/projects/{pid}/documents/{id}` | Get a document |
| PUT | `/projects/{pid}/documents/{id}` | Update a document |
| DELETE | `/projects/{pid}/documents/{id}` | Delete a document |
| POST | `/projects/{pid}/snippets` | Create a snippet |
| GET | `/projects/{pid}/snippets` | List snippets |
| GET | `/projects/{pid}/snippets/class/{name}` | List snippets by class |
| GET | `/projects/{pid}/snippets/function/{name}` | List snippets by function |
| DELETE | `/projects/{pid}/snippets/{id}` | Delete a snippet |
| POST | `/links` | Create a cross-project link |
| GET | `/links` | List all links |
| DELETE | `/links/{id}` | Delete a link |
| GET | `/search/{pid}?q=...` | Semantic search within project |
