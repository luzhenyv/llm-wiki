# llm-wiki Phase 2 Design Spec — Query System

**Date:** 2026-04-07
**Status:** Draft
**Scope:** Phase 2 — Query operation (single-shot + interactive REPL)
**Depends on:** Phase 1 — Ingest Pipeline (in progress)

---

## 1. Problem Statement

Phase 1 builds the ingest pipeline: sources go in, wiki pages come out. Phase 2 adds the query layer: the user asks questions against the wiki, the LLM searches and reads relevant pages, and synthesizes answers with inline citations. Good answers can be saved back to the wiki as new pages, making queries compound into the knowledge base.

The query system has two modes: single-shot (scriptable, composable) and an interactive REPL that serves as the **primary interaction surface** for the project — a multi-turn conversational window for deep exploration of topics.

**Core principle:** The REPL is where the user spends most of their time. It must support deep, multi-turn conversations where the LLM draws on both the wiki and its own knowledge. The tool surface is extensible — future phases add web search, web fetch, and other capabilities without changing the architecture.

## 2. Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Architecture | Thin wrapper over existing agent loop | Minimal new code, maximum reuse of Phase 1 infrastructure |
| CLI modes | Single-shot with argument, REPL without | Composable + exploratory |
| REPL role | Primary interaction surface | Users spend most time here; multi-turn deep exploration |
| Save behavior | Explicit `/save` command | Human decides what's worth persisting; no automatic prompts |
| Output rendering | Terminal only via `rich` | Phase 2 scope; saved pages go to wiki as markdown |
| Query tools | Same as ingest read-only tools | `search_wiki` + `read_page` sufficient; YAGNI |
| Citation style | Inline `[[wiki links]]` | Obsidian-native, matches wiki conventions |
| Logging | Log all queries to `wiki/log.md` | Full audit trail of exploration |
| Git commits | Only when a page is saved to wiki | Queries alone don't warrant commits |
| Multi-turn | Accumulating message history in agent loop | Follow-ups have full prior context |
| LLM knowledge | Wiki + LLM's own knowledge | Transparent about which is which |

## 3. Architecture Overview

```
llm-wiki query ["question"]
    │
    ├─ question provided? → single-shot mode
    └─ no question?       → REPL mode (loop)
    │
    ▼
┌─────────────────────────────┐
│  Build query system prompt   │
│  (schema.md + query rules)   │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  agent.run()                 │
│  Tools: search_wiki,         │
│         read_page,           │
│         ask_human,           │
│         finish_task          │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  Render answer (rich)        │
│  with [[wiki links]] inline  │
└──────────┬──────────────────┘
           ▼
┌─────────────────────────────┐
│  On /save:                   │
│  → LLM formats as wiki page │
│  → write_page + re-index     │
│  → git commit + log          │
└─────────────────────────────┘
```

**New files:** Only `llm_wiki/query.py`. The CLI dispatcher (`cli.py`) gets a `query` subcommand. Everything else is reuse.

## 4. CLI Interface

### 4.1 Single-Shot Mode

```bash
llm-wiki query "What companies have supply chain risks?"
```

Asks one question, prints the answer with inline `[[wiki links]]`, and exits. Logs the query to `wiki/log.md`.

### 4.2 Interactive REPL Mode

```bash
llm-wiki query
```

Drops into a multi-turn conversation loop. This is the primary interface for deep exploration.

**Example session:**

```
query> What's the relationship between NVDA and TSMC?
🔍 Searching wiki... reading 3 pages...

NVIDIA depends heavily on TSMC for manufacturing its GPUs...
[[NVDA]] has been TSMC's largest customer by revenue since 2023...
[... rich answer with inline citations ...]

query> Go deeper on the supply chain risk here
[... follows up with full conversation context ...]

query> /save
💾 Saving last answer to wiki...
Title? [NVDA-TSMC Supply Chain Relationship]:
→ Saved to wiki/comparisons/nvda-tsmc-supply-chain.md
→ Index updated, git committed.

query> /exit
```

### 4.3 REPL Commands

| Command | Action |
|---|---|
| `/save` | Save the last answer to the wiki (prompts for title + path) |
| `/save all` | Save the entire conversation as a single wiki page |
| `/history` | Show conversation so far |
| `/clear` | Reset conversation context, start fresh |
| `/exit`, `/quit` | Exit REPL |

### 4.4 CLI Flags (Both Modes)

| Flag | Effect |
|---|---|
| `--no-save` | Disable `/save` command (single-shot: skip save prompt entirely) |
| `--no-log` | Don't log queries to `wiki/log.md` |

### 4.5 REPL Behavior

- **Multi-turn context:** Full conversation history preserved across turns. Follow-up questions reference prior answers naturally.
- **Save is explicit:** The user types `/save` when an answer is worth persisting. No automatic prompts.
- **Wiki + LLM intelligence:** The LLM uses wiki tools when relevant, and draws on its own knowledge otherwise. It must be transparent about which is which ("Based on the wiki..." vs "In general...").
- **Extensible tool surface:** The REPL's tool list is designed to grow. Future phases add `web_search`, `web_fetch`, etc. The architecture doesn't change; tools just get added to the registry.
- **Exit:** `/exit`, `/quit`, `Ctrl+C` at prompt, or `Ctrl+D`.

## 5. Query System Prompt

The query agent receives a two-part system prompt:

### 5.1 Part 1 — `schema.md`

The project-level wiki conventions, same as ingest. Loaded from `schema.md` in the project root.

### 5.2 Part 2 — Query-Specific Instructions

Hardcoded in `query.py`, appended after `schema.md`:

```
You are a knowledge base analyst. The user will ask questions and you'll
answer by searching and reading the wiki, combined with your own knowledge.

## Rules

1. **Search before answering.** For any factual question about the wiki's
   domain, call search_wiki at least once. Don't guess at what's in the wiki.

2. **Cite your sources.** When information comes from a wiki page, use
   inline [[wiki links]]. When it comes from your own knowledge, say so
   explicitly (e.g., "Based on general knowledge..." or "The wiki doesn't
   cover this, but...").

3. **Be honest about gaps.** If the wiki doesn't have enough information,
   say so. Suggest what sources could fill the gap.

4. **Multi-turn awareness.** The user may ask follow-up questions. Use the
   full conversation context. Don't re-search for information you already
   retrieved in this session.

5. **Structured answers.** Use markdown formatting — headers, tables,
   bullet lists — when it makes the answer clearer. Keep answers focused
   and well-organized.
```

## 6. Module Design

### 6.1 New File: `llm_wiki/query.py`

Three main functions:

```python
def query_single(question: str, project_dir: str, config: dict):
    """Single-shot mode: ask one question, print answer, exit."""
    # 1. Build system prompt (schema.md + query instructions)
    # 2. Initialize indexer + tool context
    # 3. agent.run(system_prompt, question, tools=QUERY_TOOLS)
    # 4. Render answer with rich
    # 5. Log query to wiki/log.md

def query_repl(project_dir: str, config: dict):
    """Interactive REPL: multi-turn conversation loop."""
    # 1. Build system prompt + initialize tools
    # 2. Print welcome banner
    # 3. Loop:
    #    - Read user input (handle /commands)
    #    - If /save: save_answer(last_answer, ...)
    #    - If /save all: save_conversation(history, ...)
    #    - If /history: print conversation
    #    - If /clear: reset messages (keep system prompt)
    #    - If /exit: log + break
    #    - Else: run agent turn, append to history, render answer
    # 4. On exit: batch-log queries to wiki/log.md

def save_answer(answer: str, conversation: list, project_dir: str, config: dict):
    """Format answer as wiki page and save."""
    # 1. LLM call to generate: title suggestion, path suggestion, tags,
    #    formatted page content with frontmatter
    # 2. Prompt user to confirm/edit title + path
    # 3. write_page() → re-index → update index.md → git commit → log
```

### 6.2 Query Tool Set

The query agent gets a subset of the Phase 1 tools:

| Tool | Purpose |
|---|---|
| `search_wiki` | Hybrid search (FTS5 + vector) over wiki pages |
| `read_page` | Read a wiki page's full content |
| `ask_human` | Pause and ask the user a clarifying question |
| `finish_task` | Signal that the answer is complete |

These are the same tool implementations from `tools.py`. No new tools needed.

### 6.3 Integration with Existing Modules

| Module | Usage in Query | Changes Required |
|---|---|---|
| `agent.py` | ReAct loop for each query turn | Add `history` parameter (see §7) |
| `tools.py` | `search_wiki`, `read_page`, `ask_human`, `finish_task` | None |
| `indexer.py` | Hybrid search via `search_wiki` tool | None |
| `llm.py` | `chat()` for agent loop + `save_answer` formatting | None |
| `log.py` | `append()` for query logging | None |
| `git.py` | `commit()` when saving answers to wiki | None |
| `config.py` | Load project config | None |
| `cli.py` | New `query` subcommand | Add subcommand |

## 7. Agent Loop Adaptation for Multi-Turn

### 7.1 Current Signature (Phase 1)

```python
def run(system_prompt, user_prompt, tools, config) -> str:
```

### 7.2 New Signature (Backwards-Compatible)

```python
def run(system_prompt, user_prompt, tools, config, history=None) -> tuple[str, list]:
    """
    Run a ReAct tool-calling loop.

    Args:
        system_prompt: System message content.
        user_prompt: Current user message.
        tools: List of tool names to make available.
        config: Project configuration dict.
        history: Optional list of prior messages (for multi-turn REPL).
                 If None, starts fresh with [system_prompt, user_prompt].

    Returns:
        (answer_text, updated_messages) — the answer string and the full
        message history including this turn, for passing to the next call.
    """
```

**Backwards compatibility:** Phase 1 ingest still calls `run()` without `history` and can ignore the second return value. The query REPL passes `history` and accumulates it across turns.

**No other changes to existing modules.** The agent loop logic (tool calling, finish_task, ask_human) stays identical.

## 8. Save-to-Wiki Flow

When the user types `/save`:

1. **Extract the last assistant answer** from conversation history.
2. **LLM generates metadata:** A single LLM call produces a title suggestion, path suggestion (based on content type: `wiki/analyses/`, `wiki/comparisons/`, `wiki/notes/`), and tags.
3. **Prompt user to confirm/edit** title and path.
4. **LLM formats as wiki page:**
   - YAML frontmatter: `title`, `tags`, `sources` (wiki pages consulted), `last_updated`, `derived_from: "query"`
   - Content body with `[[wiki links]]` to referenced pages
   - A "Sources" section listing which wiki pages were consulted
5. **Write via `write_page`** → immediate re-index.
6. **Update `wiki/index.md`** — add the new page entry.
7. **Git commit:** `"query: saved analysis — <title>"`
8. **Log entry** appended to `wiki/log.md`.

For `/save all`, the entire conversation is formatted as a single page with each Q&A pair as a section.

## 9. Logging Format

### 9.1 Query Log Entries

Queries are logged to `wiki/log.md` using the same format as ingest:

```markdown
## [2026-04-07T15:30:00Z] query | What companies have supply chain risks?

- **Answer summary:** 3 companies identified with significant supply chain exposure...
- **Pages consulted:** concepts/supply-chain.md, entities/NVDA.md, entities/TSMC.md
- **Saved:** comparisons/supply-chain-risks.md
```

### 9.2 REPL Session Logging

- Each query in a REPL session that introduces a new topic or asks a distinct question gets its own log entry.
- Trivial follow-ups that don't change the topic ("go on", "explain more", "yes") are folded into the parent query's log entry rather than logged separately.
- The heuristic: if the user's message contains a question mark or introduces new keywords not in the prior turn, it's a new query. Otherwise it's a follow-up.
- Log entries are batch-written on REPL exit.

### 9.3 Single-Shot Logging

One log entry per invocation.

## 10. Error Handling

| Scenario | Behavior |
|---|---|
| Empty wiki (no pages indexed) | LLM answers from own knowledge, notes "wiki is empty — consider ingesting sources first" |
| LLM API unreachable | Print error, exit (single-shot) or print error and keep REPL alive |
| Search returns no results | LLM answers from own knowledge, notes "no relevant wiki pages found" |
| `/save` with no prior answer | Print "Nothing to save yet" |
| Very long answer | Render with `rich` pager (auto-pagination) |
| `Ctrl+C` during agent loop | Cancel current query, return to REPL prompt (don't exit) |
| `Ctrl+C` at REPL prompt | Exit REPL (same as `/exit`) |
| Embedding service unavailable | Fall back to FTS5-only search (graceful degradation, already in indexer) |

## 11. Dependencies

**No new dependencies.** Phase 2 uses the same dependency set as Phase 1:

| Dependency | Type | Purpose |
|---|---|---|
| `rich` | PyPI | CLI formatting, markdown rendering, REPL prompt |
| Python 3.10+ | Runtime | Standard library features |
| `sqlite3` | Stdlib | Search index (via existing indexer) |
| `urllib.request` | Stdlib | LLM API calls (via existing llm.py) |

## 12. Future Extensions (Out of Scope for Phase 2)

These are anticipated but not designed or built:

- **`web_search` tool** — Search the web from within the REPL
- **`web_fetch` tool** — Fetch and read a URL from within the REPL
- **Streaming responses** — Token-by-token rendering for long answers
- **Conversation export** — Save full REPL sessions as transcripts
- **Query templates** — Pre-built queries for common analyses
