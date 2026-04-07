"""OpenAI-compatible HTTP client using only urllib."""

import json
import urllib.request

from llm_wiki.config import DEFAULTS


def _post(url: str, payload: dict, api_key: str, timeout: int) -> dict:
    """POST JSON to url and return parsed response."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def chat(
    messages: list,
    tools: list | None = None,
    config: dict | None = None,
) -> dict:
    """Send a chat completion request to any OpenAI-compatible API."""
    cfg = (config or {}).get("llm", DEFAULTS["llm"])
    payload: dict = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.1),
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    return _post(url, payload, cfg["api_key"], cfg.get("timeout", 120))


def embed(texts: list[str], config: dict | None = None) -> list[list[float]]:
    """Get embedding vectors from an OpenAI-compatible /v1/embeddings endpoint."""
    cfg = (config or {}).get("embedding", DEFAULTS["embedding"])
    payload = {"input": texts, "model": cfg["model"]}
    url = f"{cfg['base_url'].rstrip('/')}/embeddings"
    resp = _post(url, payload, cfg["api_key"], cfg.get("timeout", 120))
    return [item["embedding"] for item in resp["data"]]
