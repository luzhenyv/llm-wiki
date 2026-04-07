"""Decorator-based tool registry for LLM function calling."""

import inspect
import typing
from pathlib import Path

_registry: dict[str, dict] = {}
_context: dict = {}

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def _infer_params(func) -> dict:
    """Build JSON schema from type hints and docstring."""
    sig = inspect.signature(func)
    hints = typing.get_type_hints(func)
    # Parse docstring for param descriptions
    descriptions: dict[str, str] = {}
    if func.__doc__:
        for line in func.__doc__.strip().splitlines():
            line = line.strip()
            if ":" in line:
                key, _, desc = line.partition(":")
                key = key.strip()
                if key in sig.parameters:
                    descriptions[key] = desc.strip()

    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name.startswith("_"):
            continue
        hint = hints.get(name, str)
        # Unwrap Optional (Union[X, None])
        origin = typing.get_origin(hint)
        if origin is typing.Union:
            args = [a for a in typing.get_args(hint) if a is not type(None)]
            hint = args[0] if args else str
        json_type = _TYPE_MAP.get(hint, "string")
        prop: dict = {"type": json_type}
        if name in descriptions:
            prop["description"] = descriptions[name]
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def tool(name: str, description: str, params: dict | None = None):
    """Register a tool with its OpenAI-compatible schema and handler."""
    def decorator(func):
        schema = params or _infer_params(func)
        _registry[name] = {
            "schema": {"type": "function", "function": {
                "name": name,
                "description": description,
                "parameters": schema,
            }},
            "handler": func,
        }
        return func
    return decorator


def get_schemas(names: list[str] | None = None) -> list[dict]:
    """Return tool schemas. If names given, filter to just those tools."""
    if names is None:
        return [t["schema"] for t in _registry.values()]
    return [_registry[n]["schema"] for n in names if n in _registry]


def execute(name: str, args: dict) -> str:
    """Route a tool call to its handler."""
    if name not in _registry:
        return f"Error: unknown tool '{name}'"
    try:
        return _registry[name]["handler"](**args)
    except Exception as e:
        return f"Error executing {name}: {e}"


def set_context(project_dir: str, indexer, config: dict):
    """Set the runtime context for tool functions."""
    _context.update(project_dir=project_dir, indexer=indexer, config=config)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@tool("search_wiki", "Search the wiki knowledge base for relevant text chunks.")
def search_wiki(query: str) -> str:
    """query: Search keywords like 'LLM architecture' or 'Project X'"""
    results = _context["indexer"].search(query)
    if not results:
        return "No relevant pages found."
    parts = []
    for r in results:
        parts.append(f"File: {r['filepath']}\nContent: {r['content']}")
    return "\n---\n".join(["Search results:"] + parts)


@tool("read_page", "Read the full content of an existing wiki page.")
def read_page(filepath: str) -> str:
    """filepath: Path to the markdown file, e.g. 'wiki/concepts/LLM.md'"""
    target = (Path(_context["project_dir"]) / filepath).resolve()
    wiki_root = (Path(_context["project_dir"]) / "wiki").resolve()
    if not str(target).startswith(str(wiki_root) + "/") and target != wiki_root:
        return f"Error: {filepath} is outside wiki directory."
    if not target.exists():
        return f"Error: {filepath} does not exist."
    return target.read_text(encoding="utf-8")


def _format_frontmatter(meta: dict) -> str:
    """Build YAML frontmatter string without pyyaml."""
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - \"{item}\"")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        else:
            lines.append(f"{key}: \"{value}\"")
    lines.append("---")
    return "\n".join(lines)


@tool("write_page", "Create or overwrite a wiki page with YAML frontmatter and markdown content.")
def write_page(filepath: str, content: str, frontmatter: dict | None = None) -> str:
    """
    filepath: Path for the wiki page, e.g. 'wiki/concepts/transformer.md'
    content: Markdown content (without YAML frontmatter)
    frontmatter: Optional dict of YAML frontmatter fields (title, tags, sources, last_updated)
    """
    normalized = Path(filepath).as_posix()
    if not normalized.startswith("wiki/"):
        return "Error: filepath must be under wiki/"
    target = (Path(_context["project_dir"]) / normalized).resolve()
    wiki_root = (Path(_context["project_dir"]) / "wiki").resolve()
    if not str(target).startswith(str(wiki_root) + "/"):
        return "Error: filepath must be under wiki/"
    target.parent.mkdir(parents=True, exist_ok=True)
    body = content
    if frontmatter:
        body = _format_frontmatter(frontmatter) + "\n\n" + content
    target.write_text(body, encoding="utf-8")
    _context["indexer"].index_file(str(target))
    return f"Page written: {filepath}"


@tool("ask_human", "Ask the human user a question when you need clarification.")
def ask_human(question: str) -> str:
    """question: The question to ask the user"""
    return "HUMAN_INPUT_REQUIRED"


@tool("finish_task", "Signal that your current task is complete.")
def finish_task(summary: str) -> str:
    """summary: Brief summary of what was accomplished"""
    return "TASK_FINISHED"
