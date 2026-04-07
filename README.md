# llm-wiki

A local, LLM-powered personal knowledge base tool inspired by [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

Feed it raw documents (10-Ks, articles, meeting notes) and it will automatically read, search, interlink, and synthesize them into a structured, [Obsidian](https://obsidian.md/)-compatible Wiki.

## Key Features

- **100% Local & Private** — SQLite FTS5 + vector hybrid search. No cloud vector databases required.
- **Minimal Dependencies** — Built on Python stdlib (`urllib.request`) + `rich`. Zero LangChain / LlamaIndex bloat.
- **Any Model** — OpenAI-compatible Tool Calling API. Works with local [Ollama](https://ollama.com/) (`qwen`, `gemma`, `llama3`) or cloud providers (OpenAI, DeepSeek, Groq).
- **Git-Transactional** — Every operation auto-commits to Git, giving you a full version history.
- **Human-in-the-Loop** — The agent pauses to ask you when ambiguity arises.
- **Obsidian-Compatible** — Wiki output is a valid Obsidian vault with `[[wiki links]]` and YAML frontmatter.

## Architecture

```
                      ┌────────────────────────────────────┐
                      │     Wiki Schema / Prompt           │
                      │         (schema.md)                │
                      └──────────────────┬─────────────────┘
                                         ▼
[ You (Terminal) ] ◄──► [ WikiAgent (ReAct Event Loop) ] ◄──► [ LLM (Ollama / Cloud) ]
                                         │
          ┌──────────────────────────────┼──────────────────────────────┐
          ▼                              ▼                              ▼
  search_wiki(query)             read_page(path)              write_page(path, ...)
          │                              │                              │
          ▼                              ▼                              ▼
  [ SQLite FTS5 + Vectors ]      [ Markdown + YAML Frontmatter ]      [ Git ]
  (.llm-wiki/wiki.db)           (wiki/)                               (auto-commit)
```

## Getting Started

### 1. Install

```bash
git clone https://github.com/your-org/llm-wiki.git
cd llm-wiki
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Start Ollama (for local models)

```bash
ollama pull qwen3.5:latest
ollama pull nomic-embed-text
ollama serve
```

### 3. Initialize a wiki project

```bash
llm-wiki init my-research
cd my-research
```

This creates the project structure:

```
my-research/
├── config.json          # LLM & embedding model settings
├── schema.md            # Wiki page conventions (editable)
├── raw/                 # Drop raw source documents here
├── wiki/
│   ├── concepts/        # Ideas, frameworks, methodologies
│   ├── entities/        # People, companies, organizations
│   ├── sources/         # One-page summaries of each source
│   ├── index.md         # Auto-maintained page catalog
│   └── log.md           # Append-only operation log
└── .llm-wiki/           # Internal state (gitignored)
    ├── wiki.db          # SQLite FTS5 + embedding vectors
    └── plans/           # Saved ingest plans (JSON)
```

### 4. Configure (optional)

Edit `config.json` to point at a different model or API:

```json
{
  "llm": {
    "base_url": "http://localhost:11434/v1",
    "model": "qwen3.5:latest",
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

For cloud providers, change `base_url` and `api_key` accordingly (e.g. `https://api.openai.com/v1` with your OpenAI key).

## Usage

### Ingest a document

```bash
# Full pipeline: plan → execute → index → commit
llm-wiki ingest raw/10K-NVDA.txt

# Generate a plan without executing (review first)
llm-wiki ingest raw/10K-NVDA.txt --plan-only

# Execute a previously saved plan
llm-wiki ingest raw/10K-NVDA.txt --execute-plan .llm-wiki/plans/20260407-article.json
```

The ingest pipeline:
1. LLM reads the source and explores the existing wiki
2. Produces a structured plan (create/update operations)
3. Generates wiki pages with YAML frontmatter and `[[wiki links]]`
4. Re-indexes all pages and updates `wiki/index.md`
5. Auto-commits to Git

### Query the wiki

```bash
# Single question
llm-wiki query "What are NVIDIA's main data center revenue drivers?"

# Interactive REPL (multi-turn conversation)
llm-wiki query
```

REPL commands:

| Command | Description |
|---|---|
| `/save` | Save last answer as a wiki page |
| `/save all` | Save entire conversation as a wiki page |
| `/history` | Show all Q&A in this session |
| `/clear` | Reset conversation context |
| `/help` | Show available commands |
| `/exit` | Exit the REPL |

Flags: `--no-save` (disable `/save`), `--no-log` (skip logging to `wiki/log.md`)

### Lint the wiki

```bash
# Full health check (structural + LLM semantic)
llm-wiki lint

# Fast structural checks only (no LLM calls)
llm-wiki lint --structural-only

# Report only, skip interactive fix mode
llm-wiki lint --no-fix
```

Checks performed:

| Check | Type | Description |
|---|---|---|
| Dead links | Structural | `[[links]]` pointing to non-existent pages |
| Orphan pages | Structural | Pages with zero inbound links |
| Missing concepts | Structural | Link targets that don't exist yet |
| Missing cross-refs | Structural | Pages mentioning a topic without linking it |
| Contradictions | Semantic | Conflicting claims across pages |
| Stale claims | Semantic | Outdated information based on source dates |
| Data gaps | Semantic | Missing subtopics identified by the LLM |

Issues found can be fixed interactively — the agent proposes edits, you approve or skip.

### Rebuild the search index

```bash
llm-wiki reindex
```

## How It Works

**Agent loop** — All operations (ingest, query, lint fix) use a generic ReAct tool-calling loop. The LLM receives a system prompt, user prompt, and available tools, then iterates: call tools → observe results → reason → repeat until done (max 20 iterations).

**Hybrid search** — Queries run against both FTS5 (BM25 keyword matching) and vector embeddings (cosine similarity), with scores normalized and merged 50/50. If the embedding service is unavailable, search gracefully degrades to FTS5-only.

**Tool registry** — Tools are plain Python functions decorated with `@tool`. Type hints and docstrings are auto-converted to OpenAI-compatible JSON schemas.

| Tool | Description |
|---|---|
| `search_wiki` | Hybrid FTS5 + vector search over wiki pages |
| `read_page` | Read full content of a wiki page |
| `write_page` | Create or overwrite a wiki page |
| `ask_human` | Pause and ask the user a question |
| `finish_task` | Signal task completion |
| `submit_plan` | Submit a structured ingest plan (ingest only) |

## License

[MIT](LICENSE)