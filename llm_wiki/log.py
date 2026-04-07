"""Append structured entries to wiki/log.md."""

import datetime
from pathlib import Path


def append(project_dir: str, operation: str, summary: str, details: dict) -> None:
    """Append an entry to wiki/log.md."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    lines = [f"## [{ts}] {operation} | {summary}", ""]

    for key in ("source", "created", "updated", "plan"):
        value = details.get(key)
        if value is None:
            continue
        label = key.capitalize()
        if isinstance(value, list):
            value = ", ".join(value)
        lines.append(f"- **{label}:** {value}")

    lines.append("")

    log_path = Path(project_dir) / "wiki" / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write("\n".join(lines))
