from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    lines = []
    for message in messages:
        role = message.get("role", "user").upper()
        content = message.get("content", "")
        lines.append(f"{role}:\n{content}")
    lines.append("ASSISTANT:")
    return "\n\n".join(lines)


def _extract_error(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return (response.text or "").strip()
    if isinstance(data, dict) and "error" in data:
        return str(data["error"]).strip()
    return (response.text or "").strip()


def ollama_chat(
    model: str,
    messages: List[Dict[str, str]],
    options: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    timeout: int = 60,
) -> str:
    base = base_url or "http://localhost:11434"
    base = base.rstrip("/")
    if base.endswith("/api"):
        base = base[:-4]
    url = f"{base}/api/chat"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if options:
        payload["options"] = options
    response = requests.post(url, json=payload, timeout=timeout)
    if response.status_code == 404:
        error_text = _extract_error(response).lower()
        if "model" in error_text and "not found" in error_text:
            raise requests.HTTPError(
                f"Ollama model not found: {model}. "
                "Set GHOST_MODEL to an installed model or run `ollama pull <model>`.",
                response=response,
            )
        generate_url = f"{base}/api/generate"
        generate_payload: Dict[str, Any] = {
            "model": model,
            "prompt": _messages_to_prompt(messages),
            "stream": False,
        }
        if options:
            generate_payload["options"] = options
        generate_response = requests.post(
            generate_url, json=generate_payload, timeout=timeout
        )
        if generate_response.status_code == 404:
            error_text = _extract_error(generate_response).lower()
            if "model" in error_text and "not found" in error_text:
                raise requests.HTTPError(
                    f"Ollama model not found: {model}. "
                    "Set GHOST_MODEL to an installed model or run `ollama pull <model>`.",
                    response=generate_response,
                )
            raise requests.HTTPError(
                "Ollama API not found at base URL. "
                "Ensure GHOST_OLLAMA_BASE_URL is like http://localhost:11434 "
                "and not suffixed with /api.",
                response=generate_response,
            )
        generate_response.raise_for_status()
        data = generate_response.json()
        return data.get("response", "")
    response.raise_for_status()
    data = response.json()
    return data.get("message", {}).get("content", "")
