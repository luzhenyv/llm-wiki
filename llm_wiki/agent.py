"""Generic ReAct tool-calling loop."""

import json

from rich.console import Console

from llm_wiki import llm
from llm_wiki.tools import execute

console = Console()

MAX_ITERATIONS = 20


def run(system_prompt: str, user_prompt: str, tool_schemas: list[dict], config: dict) -> str:
    """Run a ReAct tool-calling loop. Returns the final text or finish_task summary."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for _ in range(MAX_ITERATIONS):
        try:
            response = llm.chat(messages, tools=tool_schemas, config=config)
        except Exception as e:
            console.print(f"[bold red]LLM error: {e}[/bold red]")
            break

        choice = response["choices"][0]["message"]
        messages.append(choice)

        tool_calls = choice.get("tool_calls")
        if not tool_calls:
            return choice.get("content", "")

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
                return args.get("summary", "Done")
            else:
                result = execute(name, args)

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})

    console.print("[bold yellow]⚠ Agent reached iteration limit[/bold yellow]")
    return ""
