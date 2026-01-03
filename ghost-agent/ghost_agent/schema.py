import json
from typing import List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class WriteFileAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["write_file"]
    path: str
    content: str


class ReadFileAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["read_file"]
    path: str


class ListDirAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["list_dir"]
    path: str


class SearchInFilesAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["search_in_files"]
    path: str
    query: str


class RunCmdAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal["run_cmd"]
    cmd: str
    cwd: str


Action = Union[
    WriteFileAction,
    ReadFileAction,
    ListDirAction,
    SearchInFilesAction,
    RunCmdAction,
]


class ActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thought: str
    actions: List[Action] = Field(default_factory=list)


def parse_action_response(text: str) -> ActionResponse:
    payload = text.strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON from model") from exc

    try:
        return ActionResponse.model_validate(data)
    except ValidationError as exc:
        raise ValueError("Invalid action schema") from exc
