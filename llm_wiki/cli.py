"""CLI entry point for llm-wiki."""

import argparse
import json
import subprocess
from pathlib import Path

from rich.console import Console

from llm_wiki.config import DEFAULTS

console = Console()

_SCHEMA_TEMPLATE = """\
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
"""


def _cmd_init(directory: str):
    root = Path(directory).resolve()
    for d in [
        "raw", "wiki", "wiki/concepts", "wiki/entities", "wiki/sources",
        ".llm-wiki", ".llm-wiki/plans",
    ]:
        (root / d).mkdir(parents=True, exist_ok=True)

    (root / "config.json").write_text(
        json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8",
    )
    (root / "schema.md").write_text(_SCHEMA_TEMPLATE, encoding="utf-8")
    (root / "wiki" / "index.md").write_text("# Wiki Index\n\n*No pages yet.*\n", encoding="utf-8")
    (root / "wiki" / "log.md").write_text("# Operation Log\n", encoding="utf-8")
    (root / ".gitignore").write_text(".llm-wiki/\n__pycache__/\n*.pyc\n", encoding="utf-8")

    try:
        subprocess.run(
            ["git", "init"], cwd=str(root),
            capture_output=True, check=False,
        )
    except Exception:
        pass

    console.print(f"[bold green]✅ Wiki project initialized in {root}[/bold green]")


def _cmd_ingest(args):
    from llm_wiki.ingest import run
    run(
        project_dir=args.project_dir,
        source_file=args.source,
        plan_only=args.plan_only,
        plan_file=args.execute_plan,
    )


def _cmd_reindex(directory: str):
    from llm_wiki.config import load
    from llm_wiki.indexer import WikiIndexer

    config = load(directory)
    db_path = str(Path(directory) / ".llm-wiki" / "wiki.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    indexer = WikiIndexer(db_path, config)
    count = indexer.rebuild(str(Path(directory) / "wiki"))
    console.print(f"[bold]Reindexed {count} files[/bold]")
    indexer.close()


def _cmd_query(args):
    from llm_wiki.query import query_single, query_repl

    if args.question:
        query_single(args.question, project_dir=".", no_log=args.no_log)
    else:
        query_repl(project_dir=".", no_save=args.no_save, no_log=args.no_log)


def main():
    parser = argparse.ArgumentParser(
        prog="llm-wiki",
        description="LLM-powered personal knowledge base tool",
    )
    sub = parser.add_subparsers(dest="command")

    init_p = sub.add_parser("init", help="Initialize a new wiki project")
    init_p.add_argument("directory", nargs="?", default=".", help="Project directory (default: current)")

    ingest_p = sub.add_parser("ingest", help="Ingest a source document into the wiki")
    ingest_p.add_argument("source", help="Path to the source file")
    ingest_p.add_argument("--project-dir", default=".", help="Project directory (default: current)")
    ingest_p.add_argument("--plan-only", action="store_true", help="Generate plan without executing")
    ingest_p.add_argument("--execute-plan", metavar="PLAN", help="Execute a previously saved plan")

    reindex_p = sub.add_parser("reindex", help="Rebuild the search index from wiki files")
    reindex_p.add_argument("directory", nargs="?", default=".", help="Project directory")

    query_p = sub.add_parser("query", help="Ask questions against the wiki")
    query_p.add_argument("question", nargs="?", default=None, help="Question (omit for REPL mode)")
    query_p.add_argument("--no-save", action="store_true", help="Disable /save command in REPL")
    query_p.add_argument("--no-log", action="store_true", help="Don't log queries to wiki/log.md")

    args = parser.parse_args()

    if args.command == "init":
        _cmd_init(args.directory)
    elif args.command == "ingest":
        _cmd_ingest(args)
    elif args.command == "reindex":
        _cmd_reindex(args.directory)
    elif args.command == "query":
        _cmd_query(args)
    else:
        parser.print_help()
