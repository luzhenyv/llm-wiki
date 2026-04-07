# llm-wiki Phase 1 Design Spec — Ingest Pipeline

**Date:** 2026-04-07
**Status:** Draft
**Scope:** Phase 1 — Ingest operation only (query and lint deferred)

---

## 1. Problem Statement

Build a personal knowledge base tool that implements Andrej Karpathy's "LLM Wiki" pattern: an LLM incrementally builds and maintains a persistent, interlinked wiki from raw source documents. The tool must be framework-free, dependency-minimal, and decoupled from any specific LLM provider or IDE.

**Core principle:** Data and automated workflows are the constant. Tools, frameworks, and LLM providers change. The system must survive any of them being replaced.

## 2. Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Operations | Separate CLI commands | Composable, scriptable, automatable |
| Dependencies | `rich` only | Zero-dep HTTP via `urllib.request` |
| Config | Per-project `config.json` | Portable, inspectable |
| Directory layout | Three-layer: `raw/`, `wiki/`, `schema.md` | Follows Karpathy's architecture |
| Source of truth | Markdown files with Obsidian properties + `[[wiki links]]` | Human-readable, git-friendly, Obsidian-compatible |
| Search index | SQLite FTS5 + embeddings (hybrid) | Derived data — rebuildable from markdown |
| Operation log | `wiki/log.md` plain text, append-only | Grep-parseable, git-tracked |
| Language | All English | Open-source readability |
| Usage model | Standalone CLI (`pip install llm-wiki` / `python -m llm_wiki`) | Simple distribution |
| Git | Auto-commit after each write | Free version history |
| LLM protocol | OpenAI-compatible API only | Covers Ollama, OpenAI, DeepSeek, Groq, etc. |
| Ingest architecture | Two-phase: Plan → Execute | Auditable, resumable, editable |

## 3. Project Structure

### 3.1 Package Layout (the tool)

```
llm-wiki/
├── llm_wiki/
│   ├── __init__.py          # Version, package metadata
│   ├── __main__.py          # `python -m llm_wiki` entry point
│   ├── cli.py               # argparse CLI dispatcher
│   ├── config.py            # Load/validate config.json
│   ├── llm.py               # OpenAI-compatible HTTP client (urllib only)
│   ├── indexer.py            # SQLite FTS5 + vector hybrid search
│   ├── git.py                # Auto-commit via subprocess
│   ├── tools.py              # Decorator-based tool registry + execution router
│   ├── agent.py              # Generic ReAct tool-calling loop
│   ├── ingest.py             # Two-phase ingest workflow
│   └── log.py                # Append to wiki/log.md
├── tests/
├── pyproject.toml
├── README.md
└── llm-wiki.md
```

### 3.2 Wiki Project Layout (the data)

Created by `llm-wiki init`:

```
my-research/
├── config.json              # LLM settings, model, paths
├── schema.md                # Wiki conventions (system prompt for the LLM)
├── raw/                     # Immutable source documents
├── wiki/                    # LLM-generated markdown pages
│   ├── index.md             # Auto-maintained catalog of all pages
│   └── log.md               # Append-only operation log
└── .llm-wiki/               # Internal state (gitignored)
    ├── wiki.db              # SQLite: FTS5 index + embedding vectors
    └── plans/               # Ingest plan JSON files
```

**Invariants:**
- `raw/` is read-only to the LLM. Humans add files here.
- `wiki/` is owned by the LLM. Humans read it (in Obsidian or any editor).
- `schema.md` is co-evolved by human and LLM.
- `.llm-wiki/` is derived state. Deleting it loses nothing permanent.
- The wiki project is a valid git repo and a valid Obsidian vault.

## 4. Tool Registration System

A decorator-based registry where one decorated function = one tool. The decorator serves double duty: it registers the OpenAI-compatible JSON schema (for the LLM) and the Python handler (for local execution).

### 4.1 Core Pattern

```python
# tools.py
_registry = {}

def tool(name, description, params=None):
    """Register a tool. If params is None, auto-infer from type hints + docstring."""
    def decorator(func):
        schema = params or _infer_params(func)
        _registry[name] = {
            "schema": {"type": "function", "function": {
                "name": name,
                "description": description,
                "parameters": schema
            }},
            "handler": func
        }
        return func
    return decorator

def get_schemas():
    return [t["schema"] for t in _registry.values()]

def execute(name, args):
    return _registry[name]["handler"](**args)
```

### 4.2 Auto-Inference from Type Hints + Docstring

When `params` is omitted, the decorator inspects the function:

- **Type hints** → JSON types (`str` → `"string"`, `int` → `"integer"`, `float` → `"number"`, `bool` → `"boolean"`, `dict` → `"object"`, `list` → `"array"`)
- **Docstring lines** in `param_name: description` format → parameter descriptions
- **Parameters without defaults** → `required`

```python
@tool("search_wiki", "Search the wiki for relevant pages.")
def search_wiki(query: str) -> str:
    """query: Search keywords like 'LLM architecture' or 'Project X'"""
    ...
```

Auto-generates:
```json
{
  "type": "object",
  "properties": {
    "query": {"type": "string", "description": "Search keywords like 'LLM architecture' or 'Project X'"}
  },
  "required": ["query"]
}
```

Explicit `params` override is always available for complex schemas.

### 4.3 Phase 1 Tools

| Tool | Phase | Description |
|---|---|---|
| `search_wiki` | Plan + Execute | Hybrid search (FTS5 + vector) over wiki pages |
| `read_page` | Plan + Execute | Read a wiki page's full content |
| `write_page` | Execute only | Create or overwrite a wiki page with frontmatter |
| `ask_human` | Execute only | Pause and ask the user a question |
| `finish_task` | Execute only | Signal that the current phase is complete |

## 5. LLM Client

### 5.1 Interface

A single stateless function using `urllib.request`:

```python
def chat(messages, tools=None, config=None) -> dict:
    """Send a chat completion request to any OpenAI-compatible API."""
```

- Posts to `{base_url}/chat/completions`
- Handles tool calling (sends `tools` array, parses `tool_calls` from response)
- Timeout configurable (default 120s for local models)
- Returns the raw API response dict

### 5.2 Embeddings

```python
def embed(texts, config=None) -> list[list[float]]:
    """Get embedding vectors from an OpenAI-compatible /v1/embeddings endpoint."""
```

- Posts to `{base_url}/embeddings`
- Returns list of vectors (list of floats)
- Used by the indexer during chunk indexing

## 6. Configuration

### 6.1 config.json

```json
{
  "llm": {
    "base_url": "http://localhost:11434/v1",
    "model": "qwen3:latest",
    "api_key": "ollama",
    "temperature": 0.1,
    "timeout": 120
  },
  "embedding": {
    "base_url": "http://localhost:11434/v1",
    "model": "nomic-embed-text",
    "api_key": "ollama"
  },
  "git": {
    "auto_commit": true
  }
}
```

### 6.2 config.py

```python
def load(project_dir) -> dict:
    """Load config.json from the project directory. Falls back to sensible defaults."""
```

- Reads `config.json` from the project root
- Fills in defaults for missing keys
- Validates required keys (`llm.base_url`, `llm.model`)

## 7. Ingest Pipeline (Two-Phase)

### 7.1 Phase 1 — Plan

Triggered by `llm-wiki ingest <source-file>`.

**Inputs:**
1. Raw source file content
2. `wiki/index.md` (what pages already exist)
3. `schema.md` (wiki conventions)

**Process:**
1. Read all three inputs
2. Construct system prompt from `schema.md` + planning instructions
3. Send to LLM with read-only tools (`search_wiki`, `read_page`)
4. The agent reads the source, explores existing pages, then produces a structured plan
5. Agent calls `finish_task` with the plan as a JSON string
6. Parse and save plan to `.llm-wiki/plans/<timestamp>-<source-name>.json`
7. Print plan summary to terminal

**Plan format:**

```json
{
  "source": "raw/article.md",
  "timestamp": "2026-04-07T14:30:00Z",
  "summary": "Article about transformer architectures",
  "operations": [
    {
      "action": "create",
      "path": "wiki/concepts/transformer.md",
      "title": "Transformer Architecture",
      "tags": ["architecture", "deep-learning"],
      "brief": "Overview of the transformer architecture from Vaswani et al. 2017",
      "sources": ["raw/article.md"]
    },
    {
      "action": "update",
      "path": "wiki/concepts/attention.md",
      "reason": "New details about multi-head attention mechanism",
      "merge_hint": "Add section on scaled dot-product attention from this source"
    }
  ]
}
```

### 7.2 Phase 2 — Execute

Runs immediately after Phase 1 by default. Can be triggered independently with `--execute-plan <plan.json>`.

**Process:**
For each operation in the plan:

- **`create`**: LLM generates full page content from the brief + raw source. Writes markdown with YAML frontmatter (Obsidian properties) and `[[wiki links]]` to related pages.
- **`update`**: Reads existing page + raw source + merge hint. LLM produces the updated page, preserving existing content and integrating new information.

After all operations:
1. Rebuild/update FTS5 + embedding index
2. Append entry to `wiki/log.md`
3. Update `wiki/index.md` (add new pages, update summaries)
4. Git add + commit: `"ingest: <source-name> — created N, updated M pages"`

### 7.3 Agent Loop

Both phases use the same generic ReAct loop from `agent.py`:

```python
def run(system_prompt, user_prompt, tools, config) -> str:
    """Run a ReAct tool-calling loop until the LLM stops or calls finish_task."""
```

The loop:
1. Send messages to LLM
2. If response contains `tool_calls`: execute each tool, append results, loop
3. If response contains text only (no tool calls): return text, stop
4. If `finish_task` is called: return summary, stop
5. If `ask_human` is called: print question, wait for input, continue loop

The difference between phases is the **system prompt** and the **tools** provided.

## 8. Search Index (Hybrid)

### 8.1 Storage

Single SQLite database at `.llm-wiki/wiki.db` with two tables:

**`chunks` table (regular):**
| Column | Type | Description |
|---|---|---|
| id | INTEGER PRIMARY KEY | Auto-increment |
| filepath | TEXT | Source markdown file path |
| chunk_id | TEXT | Unique chunk identifier |
| content | TEXT | Original chunk text |
| embedding | BLOB | Packed float32 vector |

**`chunks_fts` table (FTS5 virtual):**
- Content table linked to `chunks`
- Indexed column: `content_indexed` (CJK space-inserted variant)
- Tokenizer: `unicode61`

### 8.2 Indexing

When a markdown file is indexed:
1. Split by headings (semantic chunking, same as current demo)
2. For each chunk: call `/v1/embeddings` → get vector
3. Insert chunk text + vector into SQLite

### 8.3 Hybrid Search

```python
def search(query, limit=5) -> list[dict]:
```

1. Run FTS5 BM25 query → get keyword matches with scores
2. Embed the query → compute cosine similarity against all stored vectors → get semantic matches with scores
3. Normalize both score sets to [0, 1]
4. Merge: `final_score = 0.5 * bm25_norm + 0.5 * cosine_norm` (weights configurable)
5. Return top-k results sorted by final score

### 8.4 Rebuild

```python
def rebuild(wiki_dir) -> int:
```

Drops and recreates all tables, re-indexes every `.md` file in `wiki/`. Returns count of indexed files. Called by `llm-wiki reindex`.

## 9. Git Integration

### 9.1 git.py

```python
def commit(project_dir, message):
    """Stage wiki/ changes and commit. No-op if auto_commit is off or not a git repo."""
```

- Uses `subprocess.run(["git", ...])` — no dependencies
- Only stages files under `wiki/` (not `raw/`, not `.llm-wiki/`)
- Commit message format: `"ingest: <source> — created N, updated M pages"`
- Silently skips if not inside a git repo or if `config.git.auto_commit` is false

## 10. Logging

### 10.1 wiki/log.md

Append-only. Each entry follows a consistent prefix for grep-parseability:

```markdown
## [2026-04-07T14:30:00Z] ingest | Article about Transformers

- **Source:** raw/article.md
- **Created:** concepts/transformer.md, entities/vaswani.md
- **Updated:** concepts/attention.md
- **Plan:** .llm-wiki/plans/20260407-143000-article.json
```

### 10.2 wiki/index.md

Auto-maintained catalog. Updated after each ingest. Organized by directory:

```markdown
# Wiki Index

## Concepts
- [[transformer]] — Overview of the transformer architecture (2 sources)
- [[attention]] — Attention mechanisms in neural networks (3 sources)

## Entities
- [[vaswani]] — Ashish Vaswani, key author of "Attention Is All You Need"
```

## 11. CLI Commands

### 11.1 `llm-wiki init [directory]`

Create a new wiki project:
- Creates directory structure: `raw/`, `wiki/`, `.llm-wiki/`, `.llm-wiki/plans/`
- Generates default `config.json` with Ollama defaults
- Generates starter `schema.md` with basic wiki conventions
- Creates empty `wiki/index.md` and `wiki/log.md`
- Runs `git init` if not already in a git repo
- Creates `.gitignore` with `.llm-wiki/` entry

### 11.2 `llm-wiki ingest <source-file>`

Run the two-phase ingest pipeline:
- Default: Plan → Execute → Index → Log → Git commit
- `--plan-only`: Stop after Phase 1. Print plan, save to `.llm-wiki/plans/`.
- `--execute-plan <plan.json>`: Skip Phase 1, execute a previously saved plan.

### 11.3 `llm-wiki reindex`

Rebuild the search index from scratch:
- Deletes and recreates `wiki.db`
- Re-indexes all markdown files in `wiki/`
- Re-computes all embeddings

## 12. Dependencies

| Dependency | Type | Purpose |
|---|---|---|
| `rich` | PyPI | CLI formatting, colored output, markdown rendering |
| Python 3.10+ | Runtime | f-strings, `match` statement, `pathlib`, `typing` |
| `sqlite3` | Stdlib | FTS5 search index + embedding storage |
| `urllib.request` | Stdlib | HTTP calls to LLM API |
| `json` | Stdlib | Config, plans, API payloads |
| `subprocess` | Stdlib | Git operations |
| `argparse` | Stdlib | CLI argument parsing |
| `struct` | Stdlib | Pack/unpack embedding float vectors to/from BLOB |

**Total PyPI dependencies: 1 (`rich`)**

## 13. Future Phases (Out of Scope)

These are explicitly deferred. Not designed, not built:

- **Phase 2:** `llm-wiki query "..."` — question answering against the wiki
- **Phase 3:** `llm-wiki lint` — wiki health checks
- **Phase N:** Web clipping, PDF ingestion, image handling, presentations, multi-user

## 14. Schema.md Template

The default `schema.md` created by `llm-wiki init`:

```markdown
# Wiki Schema

You are an AI knowledge base maintainer. Your workspace is a collection of
interlinked Markdown files.

## Conventions

- Every wiki page has YAML frontmatter with at least: title, tags, sources, last_updated
- Use Obsidian-style [[wiki links]] to connect related pages
- Organize pages by type: concepts/, entities/, sources/, comparisons/
- When updating a page, preserve existing content and add new information
- Flag contradictions explicitly rather than silently overwriting
- Keep pages focused — one concept or entity per page

## Directory Structure

- `wiki/concepts/` — Ideas, frameworks, methodologies
- `wiki/entities/` — People, companies, organizations, products
- `wiki/sources/` — One-page summaries of each raw source
- `wiki/comparisons/` — Side-by-side analyses
- `wiki/index.md` — Catalog of all pages (auto-maintained)
- `wiki/log.md` — Operation log (auto-maintained)
```
