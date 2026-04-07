"""Auto-commit wiki changes via subprocess."""

import subprocess

from llm_wiki.config import DEFAULTS


def commit(project_dir: str, message: str, config: dict | None = None) -> bool:
    """Stage wiki/ changes and commit. Returns True if committed, False if skipped."""
    auto = (config or {}).get("git", DEFAULTS["git"]).get("auto_commit", True)
    if not auto:
        return False
    try:
        # Check if inside a git repo
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=project_dir, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "add", "wiki/"],
            cwd=project_dir, capture_output=True, check=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=project_dir, capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False
