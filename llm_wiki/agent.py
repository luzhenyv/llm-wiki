"""Generic ReAct tool-calling loop with streaming output."""

import json

from rich.console import Console
from rich.live import Live
from rich.text import Text

from llm_wiki import llm
from llm_wiki.tools import execute

console = Console()

MAX_ITERATIONS = 20


def _collect_stream(chunks) -> dict:
    """Consume SSE chunks, print text tokens live, and return an assembled message."""
    content_parts: list[str] = []
    tool_calls_map: dict[int, dict] = {}
    role = "assistant"

    text_buf = Text()
    with Live(text_buf, console=console, refresh_per_second=15, transient=True) as live:
        for chunk in chunks:
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if "role" in delta:
                role = delta["role"]

            # Accumulate text content
            if delta.get("content"):
                token = delta["content"]
                content_parts.append(token)
                text_buf.append(token)
                live.update(text_buf)

            # Accumulate tool call fragments
            for tc_delta in delta.get("tool_calls", []):
                idx = tc_delta["index"]
                if idx not in tool_calls_map:
                    tool_calls_map[idx] = {
                        "id": tc_delta.get("id", ""),
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                entry = tool_calls_map[idx]
                if tc_delta.get("id"):
                    entry["id"] = tc_delta["id"]
                fn = tc_delta.get("function", {})
                if fn.get("name"):
                    entry["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    entry["function"]["arguments"] += fn["arguments"]

    content = "".join(content_parts)
    if content:
        console.print(content)

    message: dict = {"role": role, "content": content or None}
    if tool_calls_map:
        message["tool_calls"] = [tool_calls_map[i] for i in sorted(tool_calls_map)]
    return message


def run(system_prompt: str, user_prompt: str, tool_schemas: list[dict], config: dict, history: list | None = None) -> tuple[str, list]:
    """Run a ReAct tool-calling loop with streaming.

    Args:
        history: Optional prior messages for multi-turn. If given, system_prompt
                 is only used when history is empty (first turn).

    Returns:
        (answer_text, updated_messages) for multi-turn chaining.
    """
    if history is not None:
        messages = list(history)
        if not messages:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    for _ in range(MAX_ITERATIONS):
        try:
            chunks = llm.chat_stream(messages, tools=tool_schemas, config=config)
            choice = _collect_stream(chunks)
        except Exception as e:
            console.print(f"[bold red]LLM error: {e}[/bold red]")
            break

        messages.append(choice)

        tool_calls = choice.get("tool_calls")
        if not tool_calls:
            return choice.get("content", "") or "", messages

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except (json.JSONDecodeError, TypeError):
                args = {}

            console.print(f"[dim]⚡ {name}({json.dumps(args, ensure_ascii=False)[:80]})[/dim]")

            if name == "ask_human":
                console.print(f"\n[bold yellow]❓ {args.get('question')}[/bold yellow]")
                reply = console.input("[bold yellow]❯ [/bold yellow]")
                result = f"User replied: {reply}"
            elif name == "finish_task":
                console.print(f"\n[bold green]✅ {args.get('summary', 'Done')}[/bold green]")
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": args.get("summary", "Done")})
                return args.get("summary", "Done"), messages
            elif name == "submit_plan":
                console.print(f"\n[bold green]✅ Plan submitted[/bold green]")
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": args.get("plan_json", "{}")})
                return args.get("plan_json", "{}"), messages
            else:
                result = execute(name, args)

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})

    console.print("[bold yellow]⚠ Agent reached iteration limit[/bold yellow]")
    return "", messages
