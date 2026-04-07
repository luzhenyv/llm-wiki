"""Query system: single-shot questions and interactive REPL against the wiki."""

import json
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown

from llm_wiki import agent, git, llm, log
from llm_wiki.config import load
from llm_wiki.indexer import WikiIndexer
from llm_wiki.ingest import _update_index
from llm_wiki.tools import get_schemas, set_context, execute

console = Console()

_QUERY_INSTRUCTIONS = """\

## Your Role: Knowledge Base Analyst

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
"""

QUERY_TOOLS = ["search_wiki", "read_page", "ask_human", "finish_task"]


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _setup(project_dir: str) -> tuple[dict, WikiIndexer, str]:
    """Common setup: load config, init indexer, build system prompt."""
    config = load(project_dir)
    db_path = str(Path(project_dir) / ".llm-wiki" / "wiki.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    indexer = WikiIndexer(db_path, config)
    set_context(project_dir, indexer, config)

    wiki_dir = str(Path(project_dir) / "wiki")
    if Path(wiki_dir).exists():
        indexer.index_directory(wiki_dir)

    schema = _read_optional(Path(project_dir) / "schema.md")
    system_prompt = (schema + _QUERY_INSTRUCTIONS) if schema else _QUERY_INSTRUCTIONS
    return config, indexer, system_prompt


# ---------------------------------------------------------------------------
# Single-shot mode
# ---------------------------------------------------------------------------

def query_single(question: str, project_dir: str, no_log: bool = False):
    """Ask one question, print answer, exit."""
    config, indexer, system_prompt = _setup(project_dir)
    try:
        tool_schemas = get_schemas(QUERY_TOOLS)
        answer, _ = agent.run(system_prompt, question, tool_schemas, config)

        if not no_log:
            log.append(project_dir, "query", question, {"answer_length": len(answer)})
    finally:
        indexer.close()


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

def query_repl(project_dir: str, no_save: bool = False, no_log: bool = False):
    """Multi-turn interactive REPL."""
    config, indexer, system_prompt = _setup(project_dir)
    tool_schemas = get_schemas(QUERY_TOOLS)
    history: list[dict] = []
    queries: list[dict] = []  # for batch logging
    last_answer = ""

    console.print("[bold]llm-wiki query REPL[/bold]  (type /help for commands, /exit to quit)\n")

    try:
        while True:
            try:
                user_input = console.input("[bold cyan]query>[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            if not user_input:
                continue

            # --- REPL commands ---
            cmd = user_input.lower()

            if cmd in ("/exit", "/quit"):
                break

            if cmd == "/help":
                console.print(
                    "[dim]/save[/dim]      Save last answer to wiki\n"
                    "[dim]/save all[/dim]  Save entire conversation\n"
                    "[dim]/history[/dim]   Show conversation so far\n"
                    "[dim]/clear[/dim]     Reset conversation context\n"
                    "[dim]/exit[/dim]      Exit REPL"
                )
                continue

            if cmd == "/history":
                for msg in history:
                    if msg["role"] == "user":
                        console.print(f"[bold cyan]You:[/bold cyan] {msg['content']}")
                    elif msg["role"] == "assistant" and msg.get("content"):
                        console.print(Markdown(msg["content"]))
                    console.print()
                continue

            if cmd == "/clear":
                history = []
                last_answer = ""
                console.print("[dim]Context cleared.[/dim]")
                continue

            if cmd.startswith("/save") and not no_save:
                if not last_answer:
                    console.print("[yellow]Nothing to save yet.[/yellow]")
                    continue
                save_all = cmd == "/save all"
                save_answer(
                    last_answer if not save_all else _format_conversation(history),
                    history, project_dir, config, indexer, save_all,
                )
                continue

            if cmd.startswith("/save") and no_save:
                console.print("[yellow]Save is disabled (--no-save).[/yellow]")
                continue

            if cmd.startswith("/"):
                console.print(f"[yellow]Unknown command: {user_input}. Type /help.[/yellow]")
                continue

            # --- Run query ---
            try:
                answer, history = agent.run(
                    system_prompt, user_input, tool_schemas, config, history=history,
                )
            except KeyboardInterrupt:
                console.print("\n[dim]Query cancelled.[/dim]")
                continue

            last_answer = answer or ""
            queries.append({"question": user_input, "answer_length": len(last_answer)})

    finally:
        # Batch-log all queries on exit
        if queries and not no_log:
            for q in queries:
                log.append(project_dir, "query", q["question"], {"answer_length": q["answer_length"]})
        indexer.close()

    console.print("[dim]Goodbye![/dim]")


# ---------------------------------------------------------------------------
# Save to wiki
# ---------------------------------------------------------------------------

def save_answer(
    content: str,
    history: list[dict],
    project_dir: str,
    config: dict,
    indexer: WikiIndexer,
    is_full_conversation: bool = False,
):
    """Format answer/conversation as a wiki page and save."""
    # Ask LLM to generate metadata
    meta_prompt = (
        "Given the following content, suggest:\n"
        "1. A short title\n"
        "2. A wiki path (e.g. wiki/analyses/topic.md or wiki/notes/topic.md)\n"
        "3. A list of tags\n\n"
        "Respond with ONLY a JSON object: {\"title\": \"...\", \"path\": \"...\", \"tags\": [...]}\n\n"
        f"Content:\n{content[:2000]}"
    )
    try:
        resp = llm.chat(
            [{"role": "user", "content": meta_prompt}],
            config=config,
        )
        raw = resp["choices"][0]["message"]["content"]
        # Extract JSON from response (handle markdown code blocks)
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        meta = json.loads(raw.strip())
    except Exception:
        meta = {"title": "Untitled Query", "path": "wiki/notes/query-result.md", "tags": ["query"]}

    # Prompt user to confirm/edit
    suggested_title = meta.get("title", "Untitled")
    suggested_path = meta.get("path", "wiki/notes/query-result.md")

    console.print(f"[bold]Title?[/bold] [{suggested_title}]: ", end="")
    title_input = console.input("").strip()
    title = title_input or suggested_title

    console.print(f"[bold]Path?[/bold] [{suggested_path}]: ", end="")
    path_input = console.input("").strip()
    filepath = path_input or suggested_path

    tags = meta.get("tags", ["query"])

    # Collect source pages from history
    sources = []
    for msg in history:
        if msg["role"] == "tool" and msg.get("content", "").startswith("Search results:"):
            for line in msg["content"].splitlines():
                if line.startswith("File: "):
                    src = line[6:].strip()
                    if src not in sources:
                        sources.append(src)

    # Format as wiki page via write_page tool
    import datetime
    frontmatter = {
        "title": title,
        "tags": tags,
        "sources": sources or ["query"],
        "last_updated": datetime.date.today().isoformat(),
        "derived_from": "query",
    }

    result = execute("write_page", {
        "filepath": filepath,
        "content": content,
        "frontmatter": frontmatter,
    })
    console.print(f"[dim]{result}[/dim]")

    _update_index(project_dir)
    git.commit(project_dir, f"query: saved analysis — {title}", config)
    log.append(project_dir, "query-save", title, {"path": filepath, "sources": sources})
    console.print(f"[bold green]→ Saved to {filepath}[/bold green]")


def _format_conversation(history: list[dict]) -> str:
    """Format full conversation history as markdown content."""
    parts = []
    for msg in history:
        if msg["role"] == "user":
            parts.append(f"## Q: {msg['content']}\n")
        elif msg["role"] == "assistant" and msg.get("content"):
            parts.append(msg["content"] + "\n")
    return "\n".join(parts)
