# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import uuid
from typing import Literal, TypedDict

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command


class Todo(TypedDict):
    id: str
    title: str
    description: str
    emoji: str
    status: Literal["pending", "completed"]


class AgentState(TypedDict):
    todos: list[Todo]


@tool
def manage_todos(todos: list[Todo], runtime: ToolRuntime) -> Command:
    """
    Manage the current todos.
    Use only when the user explicitly asks to create/update/delete todos or use app mode.
    """
    for todo in todos:
        if not todo.get("id"):
            todo["id"] = str(uuid.uuid4())

    return Command(
        update={
            "todos": todos,
            "messages": [
                ToolMessage(
                    content="Successfully updated todos",
                    tool_call_id=runtime.tool_call_id,
                ),
            ],
        }
    )


@tool
def get_todos(runtime: ToolRuntime) -> list[Todo]:
    """
    Get the current todos.
    Use only for explicit todo/task requests.
    """
    todos = runtime.state.get("todos", [])
    return todos if isinstance(todos, list) else []


todo_tools = [manage_todos, get_todos]
