# Phase 3 — Lint: Wiki Health Checks

> Design spec for `llm-wiki lint` — the wiki health-check command that detects
> and interactively fixes structural and semantic issues across the knowledge base.

---

## 1. Motivation

Karpathy's original LLM Wiki pattern (llm-wiki.md, line 41):

> "**Lint.** Periodically, ask the LLM to health-check the wiki. Look for:
> contradictions between pages, stale claims that newer sources have superseded,
> orphan pages with no inbound links, important concepts mentioned but lacking
> their own page, missing cross-references, data gaps that could be filled with
> a web search."

Phase 1 built the ingest pipeline; Phase 2 added the query system. Phase 3
closes the feedback loop: the wiki inspects itself and proposes repairs.

---

## 2. Scope

### In Scope

| # | Check | Category | Severity | Fixable |
|---|-------|----------|----------|---------|
| 1 | Dead links | Structural | error | Yes |
| 2 | Orphan pages | Structural | warning | Yes |
| 3 | Missing concept pages | Structural | info | Yes |
| 4 | Missing cross-references | Structural | warning | Yes |
| 5 | Contradictions | Semantic | error | Yes |
| 6 | Stale claims | Semantic | warning | Yes |
| 7 | Data gaps | Semantic | info | Yes |

### Out of Scope

- Scheduled / cron-based lint (manual `llm-wiki lint` only)
- Web search for data gaps (future Phase 4+)
- Style or formatting linting (grammar, spelling, tone)
- Performance benchmarking of lint itself

---

## 3. Architecture

### 3.1 Module

Single module: `llm_wiki/lint.py`

### 3.2 Two-Pass Design

```
┌─────────────────────────────────────────────────┐
│                  run_lint()                      │
│                                                  │
│  Pass 1: Structural (pure Python, no LLM)       │
│    ├─ _build_link_graph()                        │
│    ├─ check_dead_links()                         │
│    ├─ check_orphans()                            │
│    ├─ check_missing_pages()                      │
│    └─ check_missing_crossrefs()                  │
│                                                  │
│  Pass 2: Semantic (LLM-powered)                  │
│    ├─ check_contradictions()                     │
│    ├─ check_stale_claims()                       │
│    └─ check_data_gaps()                          │
│                                                  │
│  Post: Report + optional interactive fix         │
│    ├─ report.to_markdown() → save                │
│    ├─ print terminal summary                     │
│    └─ fix_issues() if --no-fix not set           │
└─────────────────────────────────────────────────┘
```

### 3.3 Dependencies on Existing Modules

| Module | Usage |
|--------|-------|
| `agent.py` | `agent.run()` for semantic checks and fix proposals |
| `llm.py` | `llm.chat()` for claim extraction (non-interactive) |
| `tools.py` | `read_page`, `write_page`, `search_wiki` for fix mode |
| `config.py` | `load_config()` for wiki root, LLM settings |
| `indexer.py` | Potential reuse for content hashing |

No new external dependencies. Only `rich` (already present) for terminal output.

---

## 4. Data Model

### 4.1 LintFinding

```python
@dataclass
class LintFinding:
    check: str        # "dead_link" | "orphan" | "missing_page" | "missing_crossref"
                      # | "contradiction" | "stale_claim" | "data_gap"
    severity: str     # "error" | "warning" | "info"
    page: str         # wiki page path relative to wiki root
    message: str      # human-readable one-line description
    detail: str = ""  # extended context (e.g. contradicting claims text)
    fixable: bool = False
```

### 4.2 LintReport

```python
@dataclass
class LintReport:
    findings: list[LintFinding]
    wiki_root: Path
    timestamp: str  # ISO 8601 (datetime.now().isoformat())

    def add(self, check, severity, page, message, detail="", fixable=False):
        """Append a finding."""

    def summary(self) -> dict[str, int]:
        """Return counts keyed by severity: {"error": 3, "warning": 5, "info": 2}."""

    def by_check(self) -> dict[str, list[LintFinding]]:
        """Group findings by check name."""

    def to_markdown(self) -> str:
        """Render full report as markdown (see Section 8)."""

    def save(self, path: Path) -> None:
        """Write markdown report to file. Creates parent dirs if needed."""
```

---

## 5. Structural Checks

All structural checks are pure Python. They share a link graph built once.

### 5.1 Link Graph Builder

```python
def _build_link_graph(wiki_root: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """
    Scan all .md files under wiki_root.
    Extract [[WikiLink]] and [text](relative-path.md) references.
    
    Returns:
        (outbound, inbound) where:
        - outbound[page] = set of pages it links to
        - inbound[page] = set of pages that link to it
    
    WikiLink resolution:
        [[Some Topic]] → some-topic.md (lowercase, spaces to hyphens)
        [[path/to/page]] → path/to/page.md
    Relative markdown links resolved against the linking page's directory.
    """
```

**WikiLink pattern**: `\[\[([^\]]+)\]\]`

**Markdown link pattern**: `\[([^\]]*)\]\(([^)]+\.md)\)` (only `.md` targets)

### 5.2 Check: Dead Links

```python
def check_dead_links(wiki_root: Path, outbound: dict[str, set[str]]) -> list[LintFinding]:
```

- For each `(source, target)` in outbound, verify target file exists
- Covers **both** `[text](path.md)` relative links and `[[WikiLink]]` references
- Finding: `severity="error"`, `fixable=True`
- Fix strategy: LLM creates a stub page for the missing target, or removes the broken link
- **Relationship to missing_pages**: dead_links flags every broken link as an error.
  missing_pages (§5.4) deduplicates the `[[WikiLink]]` subset and presents them as
  info-level improvement opportunities ("these concepts deserve pages"). The two checks
  share data but serve different purposes — one is "something is broken", the other
  is "something is missing".

### 5.3 Check: Orphan Pages

```python
def check_orphans(wiki_root: Path, inbound: dict[str, set[str]]) -> list[LintFinding]:
```

- Pages with zero inbound links are orphans
- **Exceptions**: `index.md` and files in `reports/` directory are never orphans
- Finding: `severity="warning"`, `fixable=True`
- Fix strategy: LLM reads the orphan page and related pages, adds cross-references

### 5.4 Check: Missing Concept Pages

```python
def check_missing_pages(wiki_root: Path, outbound: dict[str, set[str]]) -> list[LintFinding]:
```

- Collect all `[[WikiLink]]` targets that resolve to non-existent files
- Deduplicate (same missing page may be referenced from multiple sources)
- Finding: `severity="info"`, `fixable=True`, page field = the missing page path
- Fix strategy: LLM creates the page with content derived from context

### 5.5 Check: Missing Cross-References

```python
def check_missing_crossrefs(wiki_root: Path) -> list[LintFinding]:
```

- For each page, extract topic/title from frontmatter `title` field
- Scan all other pages for case-insensitive mentions of that title as a standalone word/phrase
- If a mention exists but no link to the page → finding
- **Skip**: Self-references, pages already linked, mentions inside code blocks
- Finding: `severity="warning"`, `fixable=True`
- Fix strategy: LLM inserts `[[WikiLink]]` at the first natural mention point

---

## 6. Semantic Checks

All semantic checks call the LLM. They use `llm.chat()` for extraction steps
(no tools needed) and `agent.run()` for fix steps (needs `write_page`).

### 6.1 Lint Cache

```python
# .llm-wiki/lint-cache.json
{
    "version": 1,
    "pages": {
        "concepts/transformers.md": {
            "content_hash": "sha256:abc123...",
            "claims": [
                {"claim": "Transformers use self-attention", "section": "Overview"}
            ],
            "last_checked": "2026-04-07T10:30:00"
        }
    }
}
```

- Hash is SHA-256 of page content (excluding frontmatter `updated` field)
- If hash matches, reuse cached claims instead of re-extracting
- Cache is optional — if missing or corrupt, all pages are re-analyzed
- Cache file lives in `.llm-wiki/` (the project metadata directory)

### 6.2 Check: Contradictions

```python
def check_contradictions(wiki_root: Path, config: dict) -> list[LintFinding]:
```

**Step 1 — Claim Extraction** (per page, cacheable):
- Prompt: "Extract all factual claims from this wiki page as a JSON array. Each claim should be a self-contained statement."
- Response schema: `[{"claim": str, "section": str}]`
- Skip pages unchanged since last lint (compare content hash in cache)

**Step 2 — Topic Clustering**:
- Group claims by: (a) page tags from frontmatter, (b) page directory path
- For large wikis (>50 pages), limit comparison to pages sharing at least one tag

**Step 3 — Contradiction Detection** (per topic cluster):
- Prompt: "Review these claims from different wiki pages. Identify any contradictions — places where two claims cannot both be true. Return JSON array of contradictions."
- Response schema: `[{"claim_a": str, "page_a": str, "claim_b": str, "page_b": str, "explanation": str}]`

- Finding: `severity="error"`, `fixable=True`, detail includes both claims + explanation
- Fix strategy: LLM reads both pages, proposes which claim to keep (preferring newer source), updates the page with the stale claim

### 6.3 Check: Stale Claims

```python
def check_stale_claims(wiki_root: Path, config: dict) -> list[LintFinding]:
```

**Step 1 — Date Heuristic** (no LLM):
- Parse frontmatter for `source_date`, `created`, `updated` fields
- Find the newest `source_date` across all pages as the "freshness baseline"
- Flag pages whose `source_date` is >6 months older than the baseline
- Also flag pages with no `source_date` at all (as "unknown freshness")

**Step 2 — LLM Review** (flagged pages only):
- Prompt: "This wiki page was created from a source dated {date}. The wiki now contains sources as recent as {newest_date}. Review the content and assess: is any of this likely outdated? What might have changed?"
- Finding: `severity="warning"`, `fixable=True`
- Fix strategy: Suggest re-ingesting from updated source, or LLM annotates the page with a staleness notice

### 6.4 Check: Data Gaps

```python
def check_data_gaps(wiki_root: Path, config: dict) -> list[LintFinding]:
```

- Read `wiki/index.md` to get the topic tree
- Read a sample of pages (up to 20) for content breadth
- Prompt: "Review this knowledge base structure and sample content. What important subtopics, related concepts, or knowledge areas are missing? Focus on gaps a reader would notice."
- Page selection: read all pages if ≤20; otherwise sample by picking the first page from each directory plus random fill to 20
- Response schema: `[{"topic": str, "reason": str, "suggested_path": str}]`
- Finding: `severity="info"`, `fixable=True`, one finding per gap
- Fix strategy: LLM creates a stub page with an outline for the missing topic

---

## 7. Fix Mode

### 7.1 Flow

```
After all checks → print summary table
    │
    ▼
"Fix issues interactively? [y/N]"
    │ yes
    ▼
Iterate fixable findings (errors first, then warnings, then info):
    │
    ├─ Show finding details (rich panel)
    ├─ Call agent.run() with fix prompt + read_page/write_page/search_wiki tools
    ├─ Agent makes changes via write_page; show what was written (before/after summary)
    ├─ User confirms: [y]es (keep) / [n]o (revert) / [s]kip rest / [a]ll remaining
    │       y → apply, record as fixed
    │       n → skip this finding
    │       s → stop fixing, continue to report
    │       a → auto-approve all remaining
    ▼
Re-run affected structural checks (verify no regressions)
    │
    ▼
Append fix summary to report
```

### 7.2 Fix Agent Configuration

The fix agent uses `agent.run()` with:
- **System prompt**: "You are a wiki editor fixing a specific issue. Read the relevant pages, make the minimal change needed to resolve the finding, and use write_page to save your changes."
- **Tools**: `read_page`, `write_page`, `search_wiki`, `finish_task`
- **No** `submit_plan` or `ask_human` — fixes are atomic, one finding at a time

### 7.3 User Interaction

```
╭─ Error: Dead Link ──────────────────────────────╮
│ Page: concepts/transformers.md                   │
│ Link: [[attention-mechanisms]] → not found       │
│                                                  │
│ Proposed fix: Create stub page                   │
│ concepts/attention-mechanisms.md with outline     │
│ derived from transformer page context.           │
╰──────────────────────────────────────────────────╯
Apply fix? [y]es / [n]o / [s]kip rest / [a]ll remaining: 
```

---

## 8. Report Format

### 8.1 File Location

`wiki/reports/lint-YYYY-MM-DD.md`

Directory `wiki/reports/` is auto-created if it doesn't exist.  
If a report for today already exists, append a counter: `lint-2026-04-07-2.md`.

Reports directory is excluded from orphan checks (Section 5.3).

### 8.2 Template

```markdown
# Wiki Lint Report — {date}

## Summary

| Severity | Count | Fixed |
|----------|-------|-------|
| Error    | {n}   | {n}   |
| Warning  | {n}   | {n}   |
| Info     | {n}   | {n}   |

Total pages scanned: {n}
Structural checks: {elapsed}s
Semantic checks: {elapsed}s (using {model})

## Errors

### Dead Links
- `concepts/transformers.md` → `[[attention-mechanisms]]` (page not found)
  - **Fixed**: Created stub page `concepts/attention-mechanisms.md`

### Contradictions
- `concepts/gpt.md` § Parameters vs `concepts/scaling.md` § Model Sizes
  - Claim A: "GPT-3 has 175B parameters"
  - Claim B: "GPT-3 has 170B parameters"
  - **Fixed**: Updated `concepts/scaling.md` to 175B (citing original paper)

## Warnings

### Orphan Pages
- `notes/random-thought.md` — no inbound links
  - Not fixed

### Stale Claims
- `concepts/bert.md` — sourced from 2019, wiki has sources from 2026
  - LLM assessment: "BERT content is foundational and still accurate, but..."

### Missing Cross-References
- `concepts/llm.md` mentions "transformer" but doesn't link to `concepts/transformers.md`
  - **Fixed**: Added `[[transformers]]` link

## Info

### Missing Concept Pages
- `[[attention-mechanisms]]` referenced from 3 pages but doesn't exist

### Data Gaps
- "No page covering tokenization techniques"
- "No page covering prompt engineering patterns"
```

---

## 9. CLI Integration

### 9.1 Command

```
llm-wiki lint [--no-fix] [--no-report] [--structural-only]
```

| Flag | Effect |
|------|--------|
| `--no-fix` | Skip interactive fix mode; report only |
| `--no-report` | Terminal output only; don't write markdown report |
| `--structural-only` | Run only structural checks (no LLM calls) |

### 9.2 Terminal Output

Uses `rich.table.Table` for summary and `rich.panel.Panel` for fix interactions.

```
Scanning wiki... 42 pages found
── Structural Checks ──────────────────────────
  Dead links .......... 2 found
  Orphan pages ........ 3 found
  Missing pages ....... 1 found
  Missing cross-refs .. 5 found
── Semantic Checks ─────────────────────────────
  Contradictions ...... 1 found (scanned 42 pages, 156 claims)
  Stale claims ........ 2 found (flagged 5, confirmed 2)
  Data gaps ........... 3 found

┌──────────┬───────┬─────────┐
│ Severity │ Count │ Fixable │
├──────────┼───────┼─────────┤
│ Error    │ 3     │ 3       │
│ Warning  │ 10    │ 8       │
│ Info     │ 4     │ 4       │
└──────────┴───────┴─────────┘

Report saved to wiki/reports/lint-2026-04-07.md
```

### 9.3 Exit Codes

- `0` — No errors found (warnings/info are OK)
- `1` — Errors found (dead links, contradictions)
- `2` — Runtime failure (LLM unreachable, config missing)

---

## 10. Implementation Plan

### New Files
- `llm_wiki/lint.py` (~450-550 lines)

### Modified Files
- `llm_wiki/cli.py` — add `lint` subcommand + `_cmd_lint()` handler
- `llm_wiki/__init__.py` — add `lint` to `__all__` if applicable

### Estimated Functions

| Function | Lines | Category |
|----------|-------|----------|
| `LintFinding` (dataclass) | ~10 | Data model |
| `LintReport` (dataclass + methods) | ~60 | Data model |
| `_build_link_graph()` | ~40 | Structural |
| `check_dead_links()` | ~20 | Structural |
| `check_orphans()` | ~20 | Structural |
| `check_missing_pages()` | ~20 | Structural |
| `check_missing_crossrefs()` | ~40 | Structural |
| `_load_lint_cache()` / `_save_lint_cache()` | ~30 | Cache |
| `_extract_claims()` | ~40 | Semantic |
| `check_contradictions()` | ~60 | Semantic |
| `check_stale_claims()` | ~50 | Semantic |
| `check_data_gaps()` | ~40 | Semantic |
| `fix_issues()` | ~50 | Fix mode |
| `run_lint()` | ~40 | Orchestrator |

---

## 11. Error Handling

- **LLM unreachable during semantic checks**: Print warning, skip semantic pass, report structural findings only. Exit code 2.
- **Malformed LLM JSON response**: Retry once with stricter prompt. If still malformed, skip that check with a warning in the report.
- **Empty wiki**: Print "No pages found. Run `llm-wiki ingest` first." and exit.
- **Corrupt lint cache**: Delete cache, re-analyze all pages. Print info message.
- **Page read errors**: Skip unreadable pages, record as finding with `severity="error"`.

---

## 12. Testing Strategy

- **Structural checks**: Create a temp wiki directory with known link structures.
  Test dead links, orphans, missing pages with deterministic assertions.
- **Link graph builder**: Test WikiLink resolution (`[[Some Topic]]` → `some-topic.md`),
  relative path resolution, edge cases (self-links, anchor links).
- **Report generation**: Assert markdown output matches expected format.
- **Semantic checks**: Mock `llm.chat()` to return predefined JSON responses.
  Verify claim extraction, contradiction detection, staleness assessment.
- **Cache**: Verify cache hit/miss behavior, corruption recovery.
- **Fix mode**: Mock `agent.run()`, verify user interaction flow.
- **CLI integration**: Test argument parsing, exit codes.

---

## 13. Future Extensions (Not Phase 3)

- **`--watch` mode**: Re-lint after each ingest automatically
- **Severity thresholds**: `--fail-on warning` for CI integration
- **Web search for data gaps**: Use a web search tool to find sources for gaps
- **Diff against previous report**: Show what's new/fixed since last lint
- **Custom check plugins**: User-defined lint rules
