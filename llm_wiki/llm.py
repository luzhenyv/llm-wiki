"""OpenAI-compatible HTTP client using only urllib."""

import json
import urllib.request
from typing import Generator

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


def _stream(url: str, payload: dict, api_key: str, timeout: int) -> Generator[dict, None, None]:
    """POST JSON and yield parsed SSE chunks."""
    payload["stream"] = True
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
        buf = ""
        for raw_line in resp:
            buf += raw_line.decode("utf-8", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]":
                    return
                if line.startswith("data: "):
                    yield json.loads(line[6:])


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


def chat_stream(
    messages: list,
    tools: list | None = None,
    config: dict | None = None,
) -> Generator[dict, None, None]:
    """Yield SSE chunks from a streaming chat completion request."""
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
    yield from _stream(url, payload, cfg["api_key"], cfg.get("timeout", 120))


def embed(texts: list[str], config: dict | None = None) -> list[list[float]]:
    """Get embedding vectors from an OpenAI-compatible /v1/embeddings endpoint."""
    cfg = (config or {}).get("embedding", DEFAULTS["embedding"])
    payload = {"input": texts, "model": cfg["model"]}
    url = f"{cfg['base_url'].rstrip('/')}/embeddings"
    resp = _post(url, payload, cfg["api_key"], cfg.get("timeout", 120))
    return [item["embedding"] for item in resp["data"]]
