"""Two-phase ingest pipeline: Plan → Execute → Index → Log → Git."""

import json
import datetime
from pathlib import Path

from rich.console import Console

from llm_wiki import llm, git, log
from llm_wiki.config import load
from llm_wiki.indexer import WikiIndexer
from llm_wiki.tools import get_schemas, set_context
from llm_wiki import agent
from llm_wiki.prompts import (
    DEFAULT_SCHEMA,
    INGEST_PLAN_SYSTEM,
    INGEST_PLAN_USER,
    INGEST_CREATE_SYSTEM,
    INGEST_CREATE_USER,
    INGEST_UPDATE_SYSTEM,
    INGEST_UPDATE_USER,
)

console = Console()

_DEFAULT_SCHEMA = DEFAULT_SCHEMA


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _update_index(project_dir: str) -> None:
    """Rebuild wiki/index.md from the wiki directory tree."""
    wiki = Path(project_dir) / "wiki"
    pages: dict[str, list[tuple[str, str]]] = {}

    for md in sorted(wiki.rglob("*.md")):
        rel = md.relative_to(wiki)
        if rel.name in ("index.md", "log.md"):
            continue
        section = rel.parent.name or "Other"
        title = rel.stem.replace("-", " ").replace("_", " ").title()
        # Try extracting title from frontmatter
        try:
            text = md.read_text(encoding="utf-8")
            for line in text.splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip("\"'")
                    break
        except Exception:
            pass
        pages.setdefault(section, []).append((rel.stem, title))

    lines = ["# Wiki Index", ""]
    if not pages:
        lines.append("*No pages yet.*")
    else:
        for section, entries in sorted(pages.items()):
            lines.append(f"## {section.title()}")
            for stem, title in entries:
                lines.append(f"- [[{stem}]] — {title}")
            lines.append("")

    lines.append("")
    (wiki / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _save_plan(project_dir: str, source_file: str, plan: dict) -> Path:
    plans_dir = Path(project_dir) / ".llm-wiki" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = Path(source_file).stem
    out = plans_dir / f"{ts}-{stem}.json"
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def run(project_dir: str, source_file: str, plan_only: bool = False, plan_file: str | None = None):
    """Run the ingest pipeline."""
    # --- Setup ---
    config = load(project_dir)
    db_path = str(Path(project_dir) / ".llm-wiki" / "wiki.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    indexer = WikiIndexer(db_path, config)
    set_context(project_dir, indexer, config)

    wiki_dir = str(Path(project_dir) / "wiki")
    try:
        _run_pipeline(project_dir, source_file, plan_only, plan_file, config, indexer, wiki_dir)
    finally:
        indexer.close()


def _run_pipeline(project_dir, source_file, plan_only, plan_file, config, indexer, wiki_dir):
    if Path(wiki_dir).exists():
        indexer.index_directory(wiki_dir)

    # --- Plan phase ---
    plan = None
    if plan_file:
        plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
        console.print(f"[bold]Loaded plan from {plan_file}[/bold]")
    else:
        source_content = Path(source_file).read_text(encoding="utf-8")
        index_content = _read_optional(Path(project_dir) / "wiki" / "index.md")
        schema_path = Path(project_dir) / "schema.md"
        schema_content = _read_optional(schema_path) or _DEFAULT_SCHEMA

        system_prompt = INGEST_PLAN_SYSTEM.format(
            schema=schema_content, index_content=index_content,
        )
        user_prompt = INGEST_PLAN_USER.format(
            source_file=source_file, source_content=source_content,
        )
        console.print("[bold]Phase 1: Planning...[/bold]")
        raw, _ = agent.run(
            system_prompt, user_prompt,
            get_schemas(["search_wiki", "read_page", "submit_plan"]),
            config,
        )

        try:
            plan = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            console.print("[bold red]Failed to parse plan from agent output.[/bold red]")
            console.print(raw)
            return

        saved = _save_plan(project_dir, source_file, plan)
        console.print(f"[dim]Plan saved to {saved}[/dim]")
        for op in plan.get("operations", []):
            console.print(f"  • {op['action']} {op.get('path', '')}")

        if plan_only:
            console.print("[bold]Plan-only mode — stopping here.[/bold]")
            return

    # --- Execute phase ---
    source_content = Path(source_file).read_text(encoding="utf-8")
    schema_path = Path(project_dir) / "schema.md"
    schema_content = _read_optional(schema_path) or _DEFAULT_SCHEMA
    exec_tools = get_schemas(["search_wiki", "read_page", "write_page", "ask_human", "finish_task"])

    console.print("\n[bold]Phase 2: Executing...[/bold]")
    for i, op in enumerate(plan.get("operations", []), 1):
        action = op["action"]
        console.print(f"\n[bold cyan]({i}) {action}: {op.get('path', '')}[/bold cyan]")

        if action == "create":
            sys_prompt = INGEST_CREATE_SYSTEM.format(schema=schema_content)
            usr_prompt = INGEST_CREATE_USER.format(
                path=op['path'],
                title=op.get('title', ''),
                tags=op.get('tags', []),
                brief=op.get('brief', ''),
                sources=op.get('sources', []),
                source_content=source_content,
            )
        elif action == "update":
            sys_prompt = INGEST_UPDATE_SYSTEM.format(schema=schema_content)
            usr_prompt = INGEST_UPDATE_USER.format(
                path=op['path'],
                reason=op.get('reason', ''),
                merge_hint=op.get('merge_hint', ''),
                source_content=source_content,
            )
        else:
            console.print(f"[yellow]Unknown action: {action}, skipping[/yellow]")
            continue

        agent.run(sys_prompt, usr_prompt, exec_tools, config)  # discard history

    # --- Post-processing ---
    indexer.index_directory(wiki_dir)

    operations = plan.get("operations", [])
    n_created = sum(1 for op in operations if op["action"] == "create")
    n_updated = sum(1 for op in operations if op["action"] == "update")

    _update_index(project_dir)

    details = {
        "source": plan.get("source", source_file),
        "created": [op["path"] for op in operations if op["action"] == "create"],
        "updated": [op["path"] for op in operations if op["action"] == "update"],
    }
    log.append(project_dir, "ingest", plan.get("summary", source_file), details)

    stem = Path(source_file).stem
    git.commit(
        project_dir,
        f"ingest: {stem} — created {n_created}, updated {n_updated} pages",
        config,
    )

    console.print(f"\n[bold green]Done! Created {n_created}, updated {n_updated} pages.[/bold green]")
