"""Wiki health checks: structural and semantic lint with interactive fix mode."""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from llm_wiki import agent, llm
from llm_wiki.config import load
from llm_wiki.indexer import WikiIndexer
from llm_wiki.prompts import (
    LINT_EXTRACT_CLAIMS,
    LINT_DETECT_CONTRADICTIONS,
    LINT_STALE_WITH_DATE,
    LINT_STALE_NO_DATE,
    LINT_DATA_GAPS,
    LINT_FIX_SYSTEM,
)
from llm_wiki.tools import get_schemas, set_context

console = Console()

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
CHECK_LABELS = {
    "dead_link": "Dead Links",
    "orphan": "Orphan Pages",
    "missing_page": "Missing Concept Pages",
    "missing_crossref": "Missing Cross-References",
    "contradiction": "Contradictions",
    "stale_claim": "Stale Claims",
    "data_gap": "Data Gaps",
}
CHECK_SEVERITY_SECTION = {
    "dead_link": "Errors",
    "contradiction": "Errors",
    "orphan": "Warnings",
    "missing_crossref": "Warnings",
    "stale_claim": "Warnings",
    "missing_page": "Info",
    "data_gap": "Info",
}


@dataclass
class LintFinding:
    check: str
    severity: str
    page: str
    message: str
    detail: str = ""
    fixable: bool = False
    fixed: bool = False


@dataclass
class LintReport:
    findings: list[LintFinding] = field(default_factory=list)
    wiki_root: Path = field(default_factory=lambda: Path("."))
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    page_count: int = 0
    structural_elapsed: float = 0.0
    semantic_elapsed: float = 0.0
    model: str = ""

    def add(self, check: str, severity: str, page: str, message: str,
            detail: str = "", fixable: bool = False) -> None:
        self.findings.append(LintFinding(
            check=check, severity=severity, page=page,
            message=message, detail=detail, fixable=fixable,
        ))

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    def fixed_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
        for f in self.findings:
            if f.fixed:
                counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts

    def by_check(self) -> dict[str, list[LintFinding]]:
        grouped: dict[str, list[LintFinding]] = {}
        for f in self.findings:
            grouped.setdefault(f.check, []).append(f)
        return grouped

    def to_markdown(self) -> str:
        today = date.today().isoformat()
        s = self.summary()
        fs = self.fixed_summary()
        lines = [
            f"# Wiki Lint Report — {today}",
            "",
            "## Summary",
            "",
            "| Severity | Count | Fixed |",
            "|----------|-------|-------|",
            f"| Error    | {s['error']}     | {fs['error']}     |",
            f"| Warning  | {s['warning']}     | {fs['warning']}     |",
            f"| Info     | {s['info']}     | {fs['info']}     |",
            "",
            f"Total pages scanned: {self.page_count}",
            f"Structural checks: {self.structural_elapsed:.1f}s",
        ]
        if self.semantic_elapsed > 0:
            lines.append(
                f"Semantic checks: {self.semantic_elapsed:.1f}s"
                + (f" (using {self.model})" if self.model else "")
            )
        lines.append("")

        grouped = self.by_check()
        # Group by severity section
        for section in ("Errors", "Warnings", "Info"):
            section_checks = [
                c for c, sec in CHECK_SEVERITY_SECTION.items() if sec == section
            ]
            section_findings = []
            for c in section_checks:
                section_findings.extend(grouped.get(c, []))
            if not section_findings:
                continue
            lines.append(f"## {section}")
            lines.append("")
            for c in section_checks:
                if c not in grouped:
                    continue
                lines.append(f"### {CHECK_LABELS.get(c, c)}")
                for f in grouped[c]:
                    lines.append(f"- `{f.page}` — {f.message}")
                    if f.detail:
                        for dl in f.detail.splitlines():
                            lines.append(f"  - {dl}")
                    if f.fixed:
                        lines.append("  - **Fixed**")
                lines.append("")

        return "\n".join(lines) + "\n"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_markdown(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter fields from markdown text (no pyyaml)."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    meta: dict = {}
    current_key = ""
    current_list: list[str] | None = None

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # List item under current key
        if stripped.startswith("- ") and current_list is not None:
            val = stripped[2:].strip().strip("\"'")
            current_list.append(val)
            continue
        # Key-value pair
        if ":" in stripped:
            if current_list is not None and current_key:
                meta[current_key] = current_list
                current_list = None
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip("\"'")
            current_key = key
            if value:
                meta[key] = value
                current_list = None
            else:
                current_list = []
    if current_list is not None and current_key:
        meta[current_key] = current_list
    return meta


# ---------------------------------------------------------------------------
# Link graph builder
# ---------------------------------------------------------------------------

_RE_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_RE_MDLINK = re.compile(r"\[[^\]]*\]\(([^)]+\.md)\)")


def _resolve_wikilink(name: str, all_stems: dict[str, str]) -> str:
    """Resolve a [[WikiLink]] name to a relative path under wiki root.

    Uses the stem lookup for fuzzy matching (lowercase, hyphens).
    Returns the resolved relative path (e.g. 'concepts/llm.md') or
    the slugified name + '.md' if no match.
    """
    slug = name.lower().replace(" ", "-")
    if slug in all_stems:
        return all_stems[slug]
    # Try with .md stripped if someone wrote [[file.md]]
    if slug.endswith(".md"):
        slug = slug[:-3]
        if slug in all_stems:
            return all_stems[slug]
    return slug + ".md"


def _build_link_graph(wiki_root: Path) -> tuple[
    dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]
]:
    """Scan wiki and build link maps.

    Returns:
        (outbound, inbound, wikilink_targets) where:
        - outbound[page] = set of resolved target paths it links to
        - inbound[page] = set of pages that link to it
        - wikilink_targets[page] = set of resolved [[WikiLink]] targets only
    """
    # Build stem→relative path lookup for all existing pages
    all_stems: dict[str, str] = {}
    all_pages: set[str] = set()
    for md in wiki_root.rglob("*.md"):
        rel = str(md.relative_to(wiki_root))
        all_pages.add(rel)
        stem = md.stem.lower()
        all_stems[stem] = rel

    outbound: dict[str, set[str]] = {p: set() for p in all_pages}
    inbound: dict[str, set[str]] = {p: set() for p in all_pages}
    wikilink_targets: dict[str, set[str]] = {p: set() for p in all_pages}

    for page in all_pages:
        full = wiki_root / page
        try:
            text = full.read_text(encoding="utf-8")
        except Exception:
            continue

        # Strip code blocks to avoid false link detection
        text_no_code = re.sub(r"```[\s\S]*?```", "", text)
        text_no_code = re.sub(r"`[^`]+`", "", text_no_code)

        # WikiLinks
        for m in _RE_WIKILINK.finditer(text_no_code):
            target = _resolve_wikilink(m.group(1), all_stems)
            outbound[page].add(target)
            wikilink_targets[page].add(target)
            inbound.setdefault(target, set()).add(page)

        # Markdown links
        for m in _RE_MDLINK.finditer(text_no_code):
            href = m.group(1)
            if href.startswith("http://") or href.startswith("https://"):
                continue
            # Resolve relative to the linking page's directory
            page_dir = Path(page).parent
            resolved = str((page_dir / href).as_posix())
            # Normalize path
            resolved = str(Path(resolved))
            outbound[page].add(resolved)
            inbound.setdefault(resolved, set()).add(page)

    return outbound, inbound, wikilink_targets


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------

def check_dead_links(
    wiki_root: Path, outbound: dict[str, set[str]]
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for page, targets in outbound.items():
        for target in targets:
            if not (wiki_root / target).exists():
                findings.append(LintFinding(
                    check="dead_link", severity="error", page=page,
                    message=f"Link to '{target}' — page not found",
                    fixable=True,
                ))
    return findings


def check_orphans(
    wiki_root: Path, inbound: dict[str, set[str]]
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    all_pages = {
        str(md.relative_to(wiki_root))
        for md in wiki_root.rglob("*.md")
    }
    for page in sorted(all_pages):
        # Exceptions: index.md, log.md, reports/ directory
        name = Path(page).name
        if name in ("index.md", "log.md"):
            continue
        if page.startswith("reports/") or page.startswith("reports\\"):
            continue
        incoming = inbound.get(page, set())
        if not incoming:
            findings.append(LintFinding(
                check="orphan", severity="warning", page=page,
                message="No inbound links from other pages",
                fixable=True,
            ))
    return findings


def check_missing_pages(
    wiki_root: Path, wikilink_targets: dict[str, set[str]]
) -> list[LintFinding]:
    # Collect all missing WikiLink targets, deduplicated
    missing: dict[str, list[str]] = {}  # target → list of source pages
    for page, targets in wikilink_targets.items():
        for target in targets:
            if not (wiki_root / target).exists():
                missing.setdefault(target, []).append(page)

    findings: list[LintFinding] = []
    for target, sources in sorted(missing.items()):
        refs = ", ".join(f"`{s}`" for s in sources[:3])
        suffix = f" (+{len(sources) - 3} more)" if len(sources) > 3 else ""
        findings.append(LintFinding(
            check="missing_page", severity="info", page=target,
            message=f"Concept referenced from {refs}{suffix} but no page exists",
            fixable=True,
        ))
    return findings


def check_missing_crossrefs(wiki_root: Path) -> list[LintFinding]:
    # Build title→path map and page contents
    pages: dict[str, tuple[str, str]] = {}  # rel_path → (title, content)
    for md in wiki_root.rglob("*.md"):
        rel = str(md.relative_to(wiki_root))
        name = md.name
        if name in ("index.md", "log.md") or rel.startswith("reports/"):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = _parse_frontmatter(text)
        title = meta.get("title", md.stem.replace("-", " ").replace("_", " "))
        if len(title) < 3:  # skip very short titles
            continue
        pages[rel] = (title, text)

    # For each page, check if other pages mention its title without linking
    findings: list[LintFinding] = []
    for target_path, (title, _) in pages.items():
        target_stem = Path(target_path).stem.lower()
        pattern = re.compile(
            r"(?<!\[)\b" + re.escape(title) + r"\b(?!\])",
            re.IGNORECASE,
        )
        for other_path, (_, other_text) in pages.items():
            if other_path == target_path:
                continue
            # Skip if already linked
            other_no_code = re.sub(r"```[\s\S]*?```", "", other_text)
            other_no_code = re.sub(r"`[^`]+`", "", other_no_code)
            # Check if already linked via WikiLink or md link
            has_link = (
                f"[[{target_stem}]]" in other_no_code.lower()
                or f"[[{title.lower()}]]" in other_no_code.lower()
                or f"]({target_path})" in other_no_code
            )
            if has_link:
                continue
            # Strip frontmatter from search
            body = other_no_code
            if body.startswith("---"):
                end = body.find("---", 3)
                if end != -1:
                    body = body[end + 3:]
            if pattern.search(body):
                findings.append(LintFinding(
                    check="missing_crossref", severity="warning",
                    page=other_path,
                    message=f"Mentions '{title}' but doesn't link to `{target_path}`",
                    fixable=True,
                ))
    return findings


# ---------------------------------------------------------------------------
# Lint cache
# ---------------------------------------------------------------------------

def _cache_path(project_dir: str) -> Path:
    return Path(project_dir) / ".llm-wiki" / "lint-cache.json"


def _load_lint_cache(project_dir: str) -> dict:
    path = _cache_path(project_dir)
    if not path.exists():
        return {"version": 1, "pages": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            return {"version": 1, "pages": {}}
        return data
    except (json.JSONDecodeError, KeyError):
        console.print("[dim]Lint cache corrupt, rebuilding.[/dim]")
        return {"version": 1, "pages": {}}


def _save_lint_cache(project_dir: str, cache: dict) -> None:
    path = _cache_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def _content_hash(text: str) -> str:
    """SHA-256 hash of page content, excluding the 'updated' frontmatter field."""
    # Remove 'updated' or 'last_updated' line from frontmatter for cache stability
    clean = re.sub(r"(?m)^(last_)?updated:.*$", "", text)
    return "sha256:" + hashlib.sha256(clean.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Semantic checks
# ---------------------------------------------------------------------------

def _llm_json(prompt: str, config: dict, retries: int = 1) -> list | dict | None:
    """Call LLM with a prompt expecting JSON response. Returns parsed JSON or None."""
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(retries + 1):
        try:
            resp = llm.chat(messages, config=config)
            raw = resp["choices"][0]["message"]["content"]
            # Extract JSON from markdown code blocks
            if "```" in raw:
                parts = raw.split("```")
                for part in parts[1::2]:
                    cleaned = part.strip()
                    if cleaned.startswith("json"):
                        cleaned = cleaned[4:].strip()
                    try:
                        return json.loads(cleaned)
                    except json.JSONDecodeError:
                        continue
            return json.loads(raw.strip())
        except (json.JSONDecodeError, KeyError, TypeError):
            if attempt < retries:
                messages = [{"role": "user", "content": (
                    prompt + "\n\nIMPORTANT: Respond with ONLY valid JSON, "
                    "no markdown, no explanation."
                )}]
                continue
            return None
    return None


def check_contradictions(
    wiki_root: Path, config: dict, cache: dict
) -> list[LintFinding]:
    findings: list[LintFinding] = []
    pages_data: dict[str, dict] = {}  # rel_path → {meta, claims, tags}

    # Step 1: Extract claims per page
    for md in sorted(wiki_root.rglob("*.md")):
        rel = str(md.relative_to(wiki_root))
        if Path(rel).name in ("index.md", "log.md") or rel.startswith("reports/"):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue

        meta = _parse_frontmatter(text)
        h = _content_hash(text)
        cached = cache.get("pages", {}).get(rel, {})

        if cached.get("content_hash") == h and cached.get("claims"):
            claims = cached["claims"]
        else:
            prompt = LINT_EXTRACT_CLAIMS.format(rel=rel, text=text)
            result = _llm_json(prompt, config)
            claims = result if isinstance(result, list) else []
            # Update cache
            cache.setdefault("pages", {})[rel] = {
                "content_hash": h,
                "claims": claims,
                "last_checked": datetime.now().isoformat(),
            }

        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        pages_data[rel] = {"meta": meta, "claims": claims, "tags": tags}

    if not pages_data:
        return findings

    # Step 2: Group by topic (tags + directory)
    clusters: dict[str, list[tuple[str, list[dict]]]] = {}
    for rel, data in pages_data.items():
        if not data["claims"]:
            continue
        keys = set(data["tags"])
        keys.add(str(Path(rel).parent))
        for key in keys:
            clusters.setdefault(key, []).append((rel, data["claims"]))

    # Step 3: Detect contradictions per cluster
    for cluster_key, members in clusters.items():
        if len(members) < 2:
            continue
        # Build claims list with page attribution
        all_claims: list[str] = []
        for page, claims in members:
            for c in claims[:10]:  # limit per page
                claim_text = c.get("claim", "") if isinstance(c, dict) else str(c)
                all_claims.append(f"[{page}] {claim_text}")

        if len(all_claims) < 2:
            continue

        claims_text = "\n".join(all_claims)
        prompt = LINT_DETECT_CONTRADICTIONS.format(claims_text=claims_text)
        result = _llm_json(prompt, config)
        if not isinstance(result, list):
            continue

        for item in result:
            if not isinstance(item, dict):
                continue
            page_a = item.get("page_a", "unknown")
            page_b = item.get("page_b", "unknown")
            explanation = item.get("explanation", "")
            claim_a = item.get("claim_a", "")
            claim_b = item.get("claim_b", "")
            findings.append(LintFinding(
                check="contradiction", severity="error",
                page=f"{page_a} vs {page_b}",
                message=f"Contradicting claims between pages",
                detail=(
                    f"Claim A ({page_a}): {claim_a}\n"
                    f"Claim B ({page_b}): {claim_b}\n"
                    f"Explanation: {explanation}"
                ),
                fixable=True,
            ))

    return findings


def check_stale_claims(wiki_root: Path, config: dict) -> list[LintFinding]:
    findings: list[LintFinding] = []

    # Step 1: Date heuristic
    page_dates: dict[str, str | None] = {}  # rel → source_date or None
    for md in sorted(wiki_root.rglob("*.md")):
        rel = str(md.relative_to(wiki_root))
        if Path(rel).name in ("index.md", "log.md") or rel.startswith("reports/"):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except Exception:
            continue
        meta = _parse_frontmatter(text)
        source_date = meta.get("source_date") or meta.get("created") or meta.get("last_updated")
        page_dates[rel] = source_date

    if not page_dates:
        return findings

    # Find newest date as baseline
    valid_dates = []
    for d in page_dates.values():
        if d:
            try:
                valid_dates.append(d[:10])  # YYYY-MM-DD
            except (TypeError, IndexError):
                pass

    if not valid_dates:
        return findings

    newest = max(valid_dates)

    # Flag pages >6 months older than baseline
    try:
        newest_date = date.fromisoformat(newest)
    except ValueError:
        return findings

    flagged: list[tuple[str, str | None]] = []
    for rel, d in page_dates.items():
        if d is None:
            flagged.append((rel, None))
            continue
        try:
            page_date = date.fromisoformat(d[:10])
            delta = (newest_date - page_date).days
            if delta > 180:
                flagged.append((rel, d[:10]))
        except ValueError:
            continue

    if not flagged:
        return findings

    # Step 2: LLM review of flagged pages
    for rel, source_date in flagged:
        try:
            text = (wiki_root / rel).read_text(encoding="utf-8")
        except Exception:
            continue

        if source_date:
            prompt = LINT_STALE_WITH_DATE.format(
                source_date=source_date, newest=newest,
                rel=rel, text=text[:3000],
            )
        else:
            prompt = LINT_STALE_NO_DATE.format(
                newest=newest, rel=rel, text=text[:3000],
            )

        try:
            resp = llm.chat(
                [{"role": "user", "content": prompt}],
                config=config,
            )
            assessment = resp["choices"][0]["message"]["content"].strip()
        except Exception:
            assessment = "Could not assess (LLM error)"

        findings.append(LintFinding(
            check="stale_claim", severity="warning", page=rel,
            message=f"Source dated {source_date or 'unknown'}, newest is {newest}",
            detail=f"Assessment: {assessment}",
            fixable=True,
        ))

    return findings


def check_data_gaps(wiki_root: Path, config: dict) -> list[LintFinding]:
    findings: list[LintFinding] = []

    # Read index
    index_path = wiki_root / "index.md"
    index_content = ""
    if index_path.exists():
        index_content = index_path.read_text(encoding="utf-8")

    # Sample pages: all if ≤20, otherwise first per directory + random fill
    all_pages: list[Path] = []
    for md in sorted(wiki_root.rglob("*.md")):
        rel = str(md.relative_to(wiki_root))
        if Path(rel).name in ("index.md", "log.md") or rel.startswith("reports/"):
            continue
        all_pages.append(md)

    if len(all_pages) <= 20:
        sample = all_pages
    else:
        seen_dirs: set[str] = set()
        sample: list[Path] = []
        for p in all_pages:
            d = str(p.parent.relative_to(wiki_root))
            if d not in seen_dirs:
                sample.append(p)
                seen_dirs.add(d)
        # Fill remaining slots
        for p in all_pages:
            if len(sample) >= 20:
                break
            if p not in sample:
                sample.append(p)

    # Build content summary
    summaries: list[str] = []
    for p in sample:
        try:
            text = p.read_text(encoding="utf-8")
            meta = _parse_frontmatter(text)
            title = meta.get("title", p.stem)
            tags = meta.get("tags", [])
            rel = str(p.relative_to(wiki_root))
            summaries.append(f"- {rel}: {title} (tags: {tags})")
        except Exception:
            continue

    prompt = LINT_DATA_GAPS.format(
        index_content=index_content[:2000],
        page_count=len(sample),
        summaries="\n".join(summaries),
    )

    result = _llm_json(prompt, config)
    if not isinstance(result, list):
        return findings

    for item in result:
        if not isinstance(item, dict):
            continue
        topic = item.get("topic", "Unknown")
        reason = item.get("reason", "")
        suggested = item.get("suggested_path", "")
        findings.append(LintFinding(
            check="data_gap", severity="info",
            page=suggested or f"wiki/{topic.lower().replace(' ', '-')}.md",
            message=f"Missing topic: {topic}",
            detail=f"Reason: {reason}",
            fixable=True,
        ))

    return findings


# ---------------------------------------------------------------------------
# Fix mode
# ---------------------------------------------------------------------------

_FIX_SYSTEM = LINT_FIX_SYSTEM

_FIX_TOOLS = ["read_page", "write_page", "search_wiki", "finish_task"]


def fix_issues(
    report: LintReport, project_dir: str, config: dict, indexer: WikiIndexer
) -> int:
    """Interactive fix loop. Returns count of applied fixes."""
    fixable = [f for f in report.findings if f.fixable and not f.fixed]
    if not fixable:
        console.print("[dim]No fixable issues found.[/dim]")
        return 0

    console.print(f"\n[bold]{len(fixable)} fixable issue(s) found.[/bold]")
    try:
        answer = console.input("Fix issues interactively? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return 0

    if answer != "y":
        return 0

    # Sort: errors first, then warnings, then info
    fixable.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))

    set_context(project_dir, indexer, config)
    tool_schemas = get_schemas(_FIX_TOOLS)
    fixed_count = 0
    auto_approve = False

    for finding in fixable:
        severity_label = finding.severity.upper()
        panel = Panel(
            f"[bold]Page:[/bold] {finding.page}\n"
            f"[bold]Issue:[/bold] {finding.message}\n"
            + (f"\n{finding.detail}" if finding.detail else ""),
            title=f"{severity_label}: {CHECK_LABELS.get(finding.check, finding.check)}",
            border_style="red" if finding.severity == "error" else
                         "yellow" if finding.severity == "warning" else "blue",
        )
        console.print(panel)

        if not auto_approve:
            try:
                choice = console.input(
                    "Apply fix? [y]es / [n]o / [s]kip rest / [a]ll remaining: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                break

            if choice == "s":
                break
            if choice == "a":
                auto_approve = True
            elif choice != "y":
                continue

        # Run fix agent
        user_prompt = (
            f"Fix this issue in the wiki:\n"
            f"- Check: {finding.check}\n"
            f"- Page: {finding.page}\n"
            f"- Issue: {finding.message}\n"
            + (f"- Detail: {finding.detail}\n" if finding.detail else "")
        )

        try:
            summary, _ = agent.run(
                _FIX_SYSTEM, user_prompt, tool_schemas, config,
            )
            finding.fixed = True
            fixed_count += 1
            console.print(f"[green]✓ Fixed[/green]\n")
        except Exception as e:
            console.print(f"[red]Fix failed: {e}[/red]\n")

    return fixed_count


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _print_summary(report: LintReport) -> None:
    """Print rich summary table to terminal."""
    s = report.summary()
    total = sum(s.values())

    table = Table(title="Lint Summary")
    table.add_column("Severity", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Fixable", justify="right")

    fixable_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    for f in report.findings:
        if f.fixable:
            fixable_counts[f.severity] += 1

    table.add_row("Error", str(s["error"]), str(fixable_counts["error"]),
                   style="red" if s["error"] else None)
    table.add_row("Warning", str(s["warning"]), str(fixable_counts["warning"]),
                   style="yellow" if s["warning"] else None)
    table.add_row("Info", str(s["info"]), str(fixable_counts["info"]),
                   style="blue" if s["info"] else None)

    console.print(table)

    if total == 0:
        console.print("[bold green]✅ Wiki is clean — no issues found![/bold green]")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_lint(
    project_dir: str = ".",
    no_fix: bool = False,
    no_report: bool = False,
    structural_only: bool = False,
) -> int:
    """Run the full lint pipeline. Returns exit code (0, 1, or 2)."""
    config = load(project_dir)
    wiki_root = Path(project_dir) / "wiki"

    if not wiki_root.exists():
        console.print("[bold red]No wiki directory found. Run `llm-wiki init` first.[/bold red]")
        return 2

    # Count pages
    all_pages = list(wiki_root.rglob("*.md"))
    page_count = len([
        p for p in all_pages
        if p.name not in ("index.md", "log.md")
        and not str(p.relative_to(wiki_root)).startswith("reports/")
    ])

    if page_count == 0:
        console.print("[bold yellow]No pages found. Run `llm-wiki ingest` first.[/bold yellow]")
        return 0

    console.print(f"Scanning wiki... [bold]{page_count}[/bold] pages found\n")

    report = LintReport(
        wiki_root=wiki_root,
        page_count=page_count,
        model=config.get("llm", {}).get("model", ""),
    )

    # --- Pass 1: Structural ---
    console.print("[bold]── Structural Checks ──[/bold]")
    t0 = time.monotonic()

    outbound, inbound, wikilink_targets = _build_link_graph(wiki_root)

    dead = check_dead_links(wiki_root, outbound)
    report.findings.extend(dead)
    console.print(f"  Dead links .......... {len(dead)} found")

    orphans = check_orphans(wiki_root, inbound)
    report.findings.extend(orphans)
    console.print(f"  Orphan pages ........ {len(orphans)} found")

    missing = check_missing_pages(wiki_root, wikilink_targets)
    report.findings.extend(missing)
    console.print(f"  Missing pages ....... {len(missing)} found")

    crossrefs = check_missing_crossrefs(wiki_root)
    report.findings.extend(crossrefs)
    console.print(f"  Missing cross-refs .. {len(crossrefs)} found")

    report.structural_elapsed = time.monotonic() - t0

    # --- Pass 2: Semantic (if not --structural-only) ---
    if not structural_only:
        console.print("\n[bold]── Semantic Checks ──[/bold]")
        t1 = time.monotonic()

        cache = _load_lint_cache(project_dir)

        try:
            contradictions = check_contradictions(wiki_root, config, cache)
            report.findings.extend(contradictions)
            console.print(f"  Contradictions ...... {len(contradictions)} found")
        except Exception as e:
            console.print(f"  Contradictions ...... [red]error: {e}[/red]")

        try:
            stale = check_stale_claims(wiki_root, config)
            report.findings.extend(stale)
            console.print(f"  Stale claims ........ {len(stale)} found")
        except Exception as e:
            console.print(f"  Stale claims ........ [red]error: {e}[/red]")

        try:
            gaps = check_data_gaps(wiki_root, config)
            report.findings.extend(gaps)
            console.print(f"  Data gaps ........... {len(gaps)} found")
        except Exception as e:
            console.print(f"  Data gaps ........... [red]error: {e}[/red]")

        report.semantic_elapsed = time.monotonic() - t1
        _save_lint_cache(project_dir, cache)

    # --- Summary ---
    console.print()
    _print_summary(report)

    # --- Fix mode ---
    db_path = str(Path(project_dir) / ".llm-wiki" / "wiki.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    indexer = WikiIndexer(db_path, config)

    try:
        if not no_fix:
            fixed = fix_issues(report, project_dir, config, indexer)
            if fixed > 0:
                console.print(f"\n[bold green]{fixed} issue(s) fixed.[/bold green]")
    finally:
        indexer.close()

    # --- Report ---
    if not no_report:
        reports_dir = wiki_root / "reports"
        today = date.today().isoformat()
        report_name = f"lint-{today}.md"
        report_path = reports_dir / report_name
        # Handle duplicate: append counter
        counter = 2
        while report_path.exists():
            report_path = reports_dir / f"lint-{today}-{counter}.md"
            counter += 1
        report.save(report_path)
        console.print(f"\nReport saved to {report_path}")

    # --- Exit code ---
    errors = sum(1 for f in report.findings if f.severity == "error" and not f.fixed)
    if errors > 0:
        return 1
    return 0
