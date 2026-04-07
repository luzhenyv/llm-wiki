"""Load and validate project configuration."""

import json
from pathlib import Path

DEFAULTS = {
    "llm": {
        "base_url": "http://localhost:11434/v1",
        "model": "qwen3.5:latest",
        "api_key": "ollama",
        "temperature": 0.1,
        "timeout": 120,
    },
    "embedding": {
        "base_url": "http://localhost:11434/v1",
        "model": "nomic-embed-text",
        "api_key": "ollama",
    },
    "git": {
        "auto_commit": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load(project_dir: str) -> dict:
    """Load config.json from the project directory. Falls back to sensible defaults."""
    path = Path(project_dir) / "config.json"
    user_cfg = json.loads(path.read_text()) if path.exists() else {}
    return _deep_merge(DEFAULTS, user_cfg)
