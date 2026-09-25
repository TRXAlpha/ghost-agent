"""Validated action schema and resilient parsing for local-model responses."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, ValidationError


class WriteFileAction(BaseModel):
    tool: Literal["write_file"]
    path: str
    content: str


class ReadFileAction(BaseModel):
    tool: Literal["read_file"]
    path: str


class ListDirAction(BaseModel):
    tool: Literal["list_dir"]
    path: str = "."


class SearchInFilesAction(BaseModel):
    tool: Literal["search_in_files"]
    path: str = "."
    query: str


class RunCommandAction(BaseModel):
    tool: Literal["run_cmd"]
    cmd: str
    cwd: str = "."


class WebSearchAction(BaseModel):
    tool: Literal["web_search"]
    query: str


Action = Annotated[
    Union[
        WriteFileAction,
        ReadFileAction,
        ListDirAction,
        SearchInFilesAction,
        RunCommandAction,
        WebSearchAction,
    ],
    Field(discriminator="tool"),
]


class ActionResponse(BaseModel):
    thought: str = ""
    actions: list[Action] = Field(default_factory=list)


def _strip_markdown_fences(text: str) -> str:
    """Return the first fenced block when the model wrapped JSON in Markdown."""
    match = re.search(r"```(?:json)?\s*\n?", text, flags=re.IGNORECASE)
    if not match:
        return text.strip()
    end = text.find("```", match.end())
    return text[match.end() : end if end >= 0 else len(text)].strip()


def _extract_json_object(text: str) -> str | None:
    """Extract a balanced JSON object while respecting quoted braces."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _parse_candidate(candidate: str) -> ActionResponse:
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Qwen-sized local models often use Python triple-quoted content for a
        # multi-line write_file payload. Convert that one unambiguous form to
        # a proper JSON string before applying narrower escape repair.
        candidate = re.sub(
            r'("content"\s*:\s*)"""(.*?)"""',
            lambda match: match.group(1) + json.dumps(match.group(2)),
            candidate,
            flags=re.DOTALL,
        )
        # Local models frequently emit Windows paths such as "src\app.py" in
        # otherwise valid JSON. Escape only illegal backslashes; valid JSON
        # escapes (\n, \t, \\, etc.) retain their normal meaning.
        repaired = re.sub(r'(?<!\\)\\(?!["\\/bfnrtu])', r"\\\\", candidate)
        data = json.loads(repaired)
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object")
    # Common local-model aliases.  Keep the canonical schema internally.
    if "actions" not in data:
        for alias in ("tool_calls", "tools"):
            if alias in data:
                data["actions"] = data[alias]
                break
    return ActionResponse.model_validate(data)


def parse_action_response(response_text: str) -> ActionResponse:
    """Parse JSON despite harmless prose/fences, but reject malformed tool calls."""
    if not response_text or not response_text.strip():
        raise ValueError("Empty model response")

    candidates: list[str] = [response_text.strip(), _strip_markdown_fences(response_text)]
    extracted = _extract_json_object(response_text)
    if extracted:
        candidates.append(extracted)

    errors: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            return _parse_candidate(candidate)
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    raise ValueError("Could not parse a valid action response: " + "; ".join(errors[:2]))
