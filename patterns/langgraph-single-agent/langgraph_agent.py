# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import base64
import csv
import json
import logging
import os
import traceback
import uuid
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

# Fix OpenTelemetry bugs with streaming Bedrock tool calls.
# Individual function patches (belt-and-suspenders with the _process_event safety net).
try:
    from opentelemetry.instrumentation.botocore.extensions import bedrock_utils as _bu

    _orig_decode = _bu._decode_tool_use

    def _safe_decode_tool_use(tool_use):
        if isinstance(tool_use.get("input"), dict):
            return
        _orig_decode(tool_use)

    _bu._decode_tool_use = _safe_decode_tool_use

    _orig_extract = _bu.extract_tool_calls

    def _safe_extract_tool_calls(message, capture_content):
        content = message.get("content") if isinstance(message, dict) else None
        if not content:
            return None
        if isinstance(content, list):
            message = {**message, "content": [c for c in content if c is not None]}
        return _orig_extract(message, capture_content)

    _bu.extract_tool_calls = _safe_extract_tool_calls
except Exception:
    pass


def _apply_otel_stream_safety():
    """Make OTel stream processing fault-tolerant.

    Must be called AFTER the first botocore client is created, because
    aws-opentelemetry-distro patches _process_event lazily via
    _botocore_patches when the client is first constructed. Patching at
    module level gets overwritten.
    """
    try:
        from opentelemetry.instrumentation.botocore.extensions import bedrock_utils as _bu2
        for cls in (_bu2.ConverseStreamWrapper, _bu2.InvokeModelStreamWrapper):
            orig = cls._process_event

            def _make_safe(fn):
                def _safe_pe(self, event):
                    try:
                        fn(self, event)
                    except Exception:
                        pass
                return _safe_pe

            cls._process_event = _make_safe(orig)
    except Exception:
        pass


# Fix langchain-aws + CopilotKit streaming bugs for the Converse API:
# 1. toolUse.input stored as string instead of dict (streaming partial JSON)
# 2. CopilotKit after_model() strips frontend tool_calls from msg.tool_calls
#    but NOT from msg.content, leaving orphaned toolUse blocks that Bedrock rejects
# 3. Orphaned ToolMessages (e.g. frontend tool results sent on follow-up) that
#    have no matching tool_call in any AIMessage must be stripped
try:
    import langchain_aws.chat_models.bedrock_converse as _bc
    from langchain_core.messages import AIMessage as _AIMessage, ToolMessage as _ToolMessage

    _orig_messages_to_bedrock = _bc._messages_to_bedrock

    def _patched_messages_to_bedrock(messages):
        # Sync content with tool_calls: remove tool_use content blocks that
        # aren't in msg.tool_calls (stripped by CopilotKit after_model).
        for msg in messages:
            if isinstance(msg, _AIMessage) and isinstance(msg.content, list):
                tc_ids = {tc["id"] for tc in (msg.tool_calls or [])}
                msg.content = [
                    block for block in msg.content
                    if not (isinstance(block, dict) and block.get("type") == "tool_use"
                            and block.get("id") not in tc_ids)
                ]

        # Collect ALL valid tool_call IDs from all AIMessages (both tool_calls
        # list and content tool_use blocks). Any ToolMessage whose tool_call_id
        # isn't in this set is orphaned and would cause Bedrock to reject with
        # "toolResult blocks exceeds toolUse blocks of previous turn".
        all_tc_ids = set()
        for msg in messages:
            if isinstance(msg, _AIMessage):
                for tc in (msg.tool_calls or []):
                    all_tc_ids.add(tc["id"])
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            all_tc_ids.add(block.get("id"))

        messages = [
            msg for msg in messages
            if not (isinstance(msg, _ToolMessage) and msg.tool_call_id not in all_tc_ids)
        ]

        result = _orig_messages_to_bedrock(messages)
        # Fix string toolUse.input values from streaming
        for bedrock_msg in result[0]:
            for block in bedrock_msg.get("content", []):
                if "toolUse" in block:
                    inp = block["toolUse"].get("input")
                    if isinstance(inp, str):
                        try:
                            block["toolUse"]["input"] = json.loads(inp) if inp else {}
                        except (json.JSONDecodeError, TypeError):
                            block["toolUse"]["input"] = {}
                    elif inp is None:
                        block["toolUse"]["input"] = {}
        return result

    _bc._messages_to_bedrock = _patched_messages_to_bedrock
except Exception:
    pass

import uvicorn
from ag_ui.core import RunAgentInput, RunErrorEvent, RunFinishedEvent
from ag_ui.encoder import EventEncoder
from ag_ui_langgraph import LangGraphAgent
from copilotkit import CopilotKitMiddleware
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain.agents import create_agent
from langchain.tools import ToolRuntime, tool
from langchain_aws import ChatBedrock
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables.config import ensure_config
from langgraph.types import Command
from langgraph.checkpoint.base import CheckpointTuple
from langgraph_checkpoint_aws import AgentCoreMemorySaver

logger = logging.getLogger("langgraph_single_agent")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
logger.setLevel(logging.INFO)

app = FastAPI()

ACTOR_ID_KEYS = ("actor_id", "actorId", "user_id", "userId", "sub")


class Todo(TypedDict):
    id: str
    title: str
    description: str
    emoji: str
    status: Literal["pending", "completed"]


class AgentState(TypedDict):
    todos: list[Todo]


def _csv_path() -> Path:
    return Path(__file__).resolve().parent / "db.csv"


def _load_rows() -> list[dict[str, str]]:
    with _csv_path().open(encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, str]] = []
        for row in reader:
            normalized = {key: value for key, value in row.items() if key is not None}
            extra = row.get(None)
            if extra:
                normalized["notes_extra"] = ", ".join(extra)
            rows.append(normalized)
    return rows


_CACHED_ROWS = _load_rows()


def _aggregate(
    rows: list[dict[str, str]],
    kind: str,
) -> list[dict[str, float | str]]:
    totals: dict[str, float] = {}
    for row in rows:
        row_type = (row.get("type") or "").strip().lower()
        include = (kind == "revenue" and row_type == "income") or (
            kind == "expenses" and row_type == "expense"
        )
        if not include:
            continue

        label = (row.get("subcategory") or row.get("category") or "Unknown").strip()
        amount_raw = (row.get("amount") or "0").replace(",", "").strip()
        try:
            amount = float(amount_raw)
        except ValueError:
            amount = 0.0
        totals[label] = totals.get(label, 0.0) + amount

    return [
        {"label": label, "value": round(value, 2)}
        for label, value in sorted(
            totals.items(), key=lambda item: item[1], reverse=True
        )
    ]


@tool
def query_data(query: str) -> dict[str, Any]:
    """
    Query the database. Always call this before showing a chart or graph.
    """
    query_preview = (query or "").strip().replace("\n", " ")
    if len(query_preview) > 200:
        query_preview = f"{query_preview[:200]}..."
    logger.info("[TOOL query_data] start query=%s", query_preview or "<empty>")

    # Copy row dicts so callers cannot mutate module-level cached data.
    rows = [dict(row) for row in _CACHED_ROWS]
    query_lower = query.lower()

    if "expense" in query_lower:
        selected_view = "expenses_by_subcategory"
        data = _aggregate(rows, "expenses")
    else:
        selected_view = "revenue_by_subcategory"
        data = _aggregate(rows, "revenue")

    result = {
        "rows": rows,
        "selected_view": selected_view,
        "data": data,
        "available_views": {
            "revenue_by_subcategory": _aggregate(rows, "revenue"),
            "expenses_by_subcategory": _aggregate(rows, "expenses"),
        },
        "notes": [
            "SQL statements are not executed.",
            "rows contains the full dataset from db.csv.",
            "Use selected_view + data to render the requested chart.",
        ],
        "raw_row_count": len(rows),
    }

    logger.info(
        "[TOOL query_data] end selected_view=%s points=%s raw_row_count=%s",
        selected_view,
        len(data),
        len(rows),
    )
    return result


@tool
def manage_todos(todos: list[Todo], runtime: ToolRuntime) -> Command:
    """
    Manage the current todos.
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
                    name="manage_todos",
                ),
            ],
        }
    )


@tool
def get_todos(runtime: ToolRuntime) -> list[Todo]:
    """
    Get the current todos.
    """
    todos = runtime.state.get("todos", [])
    return todos if isinstance(todos, list) else []


TODO_TOOLS = [manage_todos, get_todos]

SYSTEM_PROMPT = """
You are a helpful assistant that helps users understand CopilotKit and LangGraph used together.

Be brief in your explanations of CopilotKit and LangGraph, 1 to 2 sentences.

When demonstrating charts:
1. Always call the query_data tool to fetch data first.
2. Then call the relevant frontend chart tool (show_pie_chart or show_bar_chart) with chart-ready data.
3. Do not call query_data repeatedly with SQL variations in the same run.

Todo tools policy:
1. Only use get_todos or manage_todos when the user explicitly asks about todos, tasks, or app/canvas mode.
2. Never call get_todos or manage_todos for chart/theme/general Q&A requests.

Scheduling policy:
1. If the user asks to schedule a meeting, pick a meeting time, or explicitly asks for human-in-the-loop scheduling, call the frontend tool scheduleTime.
2. Provide scheduleTime arguments:
   - reasonForScheduling: short reason (about 3-5 words)
   - meetingDuration: use an integer number of minutes (default to 30 if user did not specify)
3. After calling scheduleTime, stop and wait for user interaction.

Stop condition:
- After calling the required frontend tool for the user request, stop calling tools and finish the run.
""".strip()


def decode_jwt_sub(authorization_header: str | None) -> str | None:
    if not authorization_header:
        return None

    parts = authorization_header.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    token_parts = parts[1].split(".")
    if len(token_parts) < 2:
        return None

    try:
        payload = token_parts[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8"))
        sub = json.loads(decoded).get("sub")
        return sub if isinstance(sub, str) and sub else None
    except Exception:
        return None


def resolve_actor_id(
    input_data: RunAgentInput, authorization_header: str | None
) -> str | None:
    forwarded_props = (
        input_data.forwarded_props
        if isinstance(input_data.forwarded_props, dict)
        else {}
    )

    for key in ACTOR_ID_KEYS:
        value = forwarded_props.get(key)
        if isinstance(value, str) and value:
            return value

    return decode_jwt_sub(authorization_header)


def _build_model(streaming: bool) -> ChatBedrock:
    return ChatBedrock(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        temperature=0.1,
        max_tokens=16384,
        streaming=streaming,
        beta_use_converse_api=True,
    )


class ActorAwareLangGraphAgent(LangGraphAgent):
    def set_message_in_progress(self, run_id, data):
        """Fix ag_ui_langgraph bug: messages_in_process[run_id] can be None."""
        current = self.messages_in_process.get(run_id) or {}
        self.messages_in_process[run_id] = {**current, **data}

    def langgraph_default_merge_state(
        self, state: dict[str, Any], messages: list[Any], input: RunAgentInput
    ) -> dict[str, Any]:
        merged_state = super().langgraph_default_merge_state(state, messages, input)

        # Fix orphaned frontend tool_calls in checkpoint AIMessages.
        # CopilotKit middleware restores frontend tool_calls (e.g. enableAppMode)
        # to AIMessages after the run but doesn't add ToolMessages. On follow-up
        # requests the frontend sends ToolMessages for these, which get appended
        # at the wrong position causing Bedrock API errors.
        #
        # Strategy: strip orphaned tool_calls from checkpoint AIMessages and
        # filter their stray ToolMessages from new messages. However, if the
        # AIMessage is at the TAIL of the checkpoint (no non-ToolMessage messages
        # after it), an incoming ToolMessage can be safely appended — so we
        # keep those tool_calls to avoid breaking the continuation loop for
        # frontend tools like show_pie_chart.
        checkpoint_messages = state.get("messages", [])
        new_messages = merged_state.get("messages", [])

        checkpoint_tool_result_ids = {
            msg.tool_call_id
            for msg in checkpoint_messages
            if isinstance(msg, ToolMessage)
        }
        new_tool_result_ids = {
            msg.tool_call_id
            for msg in new_messages
            if isinstance(msg, ToolMessage)
        }

        orphan_tool_call_ids: set[str] = set()
        replacement_ai_messages: list[AIMessage] = []
        for idx, msg in enumerate(checkpoint_messages):
            if not (isinstance(msg, AIMessage) and msg.tool_calls):
                continue

            # Find tool_calls with no ToolMessage in the checkpoint
            unmatched = {
                tc["id"] for tc in msg.tool_calls
                if tc["id"] not in checkpoint_tool_result_ids
            }
            if not unmatched:
                continue

            # Can we safely append ToolMessages after this AIMessage?
            # Only if there are no non-ToolMessage messages after it
            # (ToolMessages for THIS AIMessage's tool_calls are OK).
            ai_tc_ids = {tc["id"] for tc in msg.tool_calls}
            can_append = True
            for later in checkpoint_messages[idx + 1:]:
                if isinstance(later, ToolMessage) and later.tool_call_id in ai_tc_ids:
                    continue
                can_append = False
                break

            if can_append:
                # Only strip unmatched tool_calls that have NO incoming ToolMessage
                to_strip = unmatched - new_tool_result_ids
            else:
                # Can't append at the right position — strip ALL unmatched
                to_strip = unmatched

            if to_strip:
                orphan_tool_call_ids.update(to_strip)
                kept = [tc for tc in msg.tool_calls if tc["id"] not in to_strip]
                new_content = msg.content
                if isinstance(new_content, list):
                    new_content = [
                        block for block in new_content
                        if not (
                            isinstance(block, dict)
                            and block.get("type") == "tool_use"
                            and block.get("id") in to_strip
                        )
                    ]
                replacement_ai_messages.append(
                    msg.model_copy(update={
                        "tool_calls": kept,
                        "content": new_content,
                    })
                )
                logger.info(
                    "[MERGE] Will replace AIMessage %s to strip orphan tool_calls: %s",
                    msg.id,
                    to_strip,
                )

        if orphan_tool_call_ids:
            filtered = list(replacement_ai_messages)
            for msg in new_messages:
                if isinstance(msg, ToolMessage) and msg.tool_call_id in orphan_tool_call_ids:
                    logger.info(
                        "[MERGE] Filtering stray ToolMessage tool_call_id=%s",
                        msg.tool_call_id,
                    )
                    continue
                filtered.append(msg)
            merged_state["messages"] = filtered
        # else: no orphans, pass through new_messages unchanged

        tools = merged_state.get("tools", [])
        copilotkit_state = merged_state.get("copilotkit", {})
        if not isinstance(copilotkit_state, dict):
            copilotkit_state = {}

        # CopilotKitMiddleware expects frontend tools under state.copilotkit.actions.
        merged_state["copilotkit"] = {
            **copilotkit_state,
            "actions": tools,
            "context": input.context or [],
        }
        return merged_state

    async def get_checkpoint_before_message(self, message_id: str, thread_id: str):
        if not thread_id:
            raise ValueError("Missing thread_id in config")

        config = ensure_config(self.config.copy() if self.config else {})
        configurable = dict(config.get("configurable", {}))
        configurable["thread_id"] = thread_id

        history_list = []
        async for snapshot in self.graph.aget_state_history(
            {"configurable": configurable}
        ):
            history_list.append(snapshot)

        history_list.reverse()
        for idx, snapshot in enumerate(history_list):
            messages = snapshot.values.get("messages", [])
            if any(getattr(m, "id", None) == message_id for m in messages):
                if idx == 0:
                    empty_snapshot = snapshot
                    empty_snapshot.values["messages"] = []
                    return empty_snapshot

                snapshot_values_without_messages = snapshot.values.copy()
                del snapshot_values_without_messages["messages"]
                checkpoint = history_list[idx - 1]

                merged_values = {**checkpoint.values, **snapshot_values_without_messages}
                checkpoint = checkpoint._replace(values=merged_values)
                return checkpoint

        raise ValueError("Message ID not found in history")


class _NoPatchEventProcessor:
    """EventProcessor that skips patch_orphan_tool_calls.

    CopilotKit frontend tool calls are intentionally orphaned — the frontend
    executes them client-side and adds the real ToolMessage on the next run.
    patch_orphan_tool_calls would inject fake error ToolMessages that conflict
    with the real results.

    Orphaned tool_calls are instead handled at merge time in
    ActorAwareLangGraphAgent.langgraph_default_merge_state.
    """

    def __init__(self, original_processor):
        self._original = original_processor

    def process_events(self, events):
        return self._original.process_events(events)

    def build_checkpoint_tuple(self, checkpoint_event, writes, channel_data, config):
        pending_writes = [
            (write.task_id, write.channel, write.value) for write in writes
        ]
        parent_config = None
        if checkpoint_event.parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": config.thread_id,
                    "actor_id": config.actor_id,
                    "checkpoint_ns": config.checkpoint_ns,
                    "checkpoint_id": checkpoint_event.parent_checkpoint_id,
                }
            }

        checkpoint = checkpoint_event.checkpoint_data.copy()
        channel_values = {}
        for channel, version in checkpoint.get("channel_versions", {}).items():
            if (channel, version) in channel_data:
                channel_values[channel] = channel_data[(channel, version)]

        # NOTE: We intentionally skip patch_orphan_tool_calls here.
        # Orphaned frontend tool_calls are handled in langgraph_default_merge_state
        # by filtering stray ToolMessages instead.

        checkpoint["channel_values"] = channel_values

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": config.thread_id,
                    "actor_id": config.actor_id,
                    "checkpoint_ns": config.checkpoint_ns,
                    "checkpoint_id": checkpoint_event.checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=checkpoint_event.metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )


class CopilotKitMemorySaver(AgentCoreMemorySaver):
    """AgentCoreMemorySaver that skips patch_orphan_tool_calls entirely."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.processor = _NoPatchEventProcessor(self.processor)


def _build_checkpointer() -> CopilotKitMemorySaver:
    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    return CopilotKitMemorySaver(
        memory_id=memory_id,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def _build_agui_graph():
    logger.info("[DEPLOY_CHECK] v28 - strip ALL orphaned toolResults + deferred OTel safety")
    graph = create_agent(
        model=_build_model(streaming=True),
        tools=[query_data, *TODO_TOOLS],
        checkpointer=_build_checkpointer(),
        middleware=[CopilotKitMiddleware()],
        state_schema=AgentState,
        system_prompt=SYSTEM_PROMPT,
    )
    # Apply OTel safety AFTER create_agent triggers botocore client creation,
    # which is when _botocore_patches applies its _process_event patches.
    _apply_otel_stream_safety()
    return graph


@app.on_event("startup")
async def startup_event() -> None:
    try:
        graph = _build_agui_graph()
        app.state.agui_agent = ActorAwareLangGraphAgent(
            name="LangGraphSingleAgent",
            description="LangGraph single agent exposed via AG-UI",
            graph=graph,
        )
        logger.info("[STARTUP] AG-UI endpoint mounted at POST /invocations")
    except Exception as exc:
        logger.error("[STARTUP ERROR] Failed to initialize AG-UI graph: %s", exc)
        traceback.print_exc()
        raise


@app.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "Healthy"}


async def _handle_agui(payload: dict[str, Any], request: Request):
    try:
        input_data = RunAgentInput.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid AG-UI payload: {exc}"
        ) from exc

    thread_id = getattr(input_data, "thread_id", None) or "unknown"
    run_id = getattr(input_data, "run_id", None) or "unknown"
    actor_id = resolve_actor_id(input_data, request.headers.get("authorization"))

    if not actor_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing actor identity. Provide forwardedProps.actor_id/user_id "
                "or Authorization Bearer token with sub claim."
            ),
        )

    logger.info(
        "[AGUI] received thread_id=%s run_id=%s actor_id=%s messages=%s tools=%s",
        thread_id,
        run_id,
        actor_id,
        len(getattr(input_data, "messages", []) or []),
        len(getattr(input_data, "tools", []) or []),
    )

    base_agent = cast(ActorAwareLangGraphAgent, app.state.agui_agent)
    request_agent = ActorAwareLangGraphAgent(
        name=base_agent.name,
        description=base_agent.description,
        graph=base_agent.graph,
        config={"configurable": {"actor_id": actor_id}},
    )

    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_generator():
        event_count = 0
        saw_terminal_event = False

        try:
            async for event in request_agent.run(input_data):
                event_count += 1
                if isinstance(event, dict):
                    event_type_raw = event.get("type")
                else:
                    event_type_raw = getattr(event, "type", None)
                if hasattr(event_type_raw, "value"):
                    event_type = str(event_type_raw.value)
                elif event_type_raw:
                    event_type = str(event_type_raw)
                else:
                    event_type = "UNKNOWN"

                if event_type in {
                    "RUN_STARTED",
                    "RUN_FINISHED",
                    "RUN_ERROR",
                    "TOOL_CALL_START",
                    "TOOL_CALL_RESULT",
                }:
                    logger.info(
                        "[AGUI] event thread_id=%s run_id=%s event=%s count=%s",
                        thread_id,
                        run_id,
                        event_type,
                        event_count,
                    )

                if event_type in {"RUN_FINISHED", "RUN_ERROR"}:
                    saw_terminal_event = True

                yield encoder.encode(event)
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                logger.info(
                    "[AGUI] stream cancelled thread_id=%s run_id=%s",
                    thread_id,
                    run_id,
                )
                return
            saw_terminal_event = True
            tb_str = traceback.format_exc()
            logger.exception(
                "[AGUI] stream failure thread_id=%s run_id=%s error=%s",
                thread_id,
                run_id,
                exc,
            )
            yield encoder.encode(
                RunErrorEvent(
                    message=f"{type(exc).__name__}: {exc}\n\n{tb_str}",
                    code=type(exc).__name__,
                )
            )

        if not saw_terminal_event:
            logger.error(
                "[AGUI] missing terminal event thread_id=%s run_id=%s total_events=%s",
                thread_id,
                run_id,
                event_count,
            )
            yield encoder.encode(
                RunFinishedEvent(
                    threadId=thread_id,
                    runId=run_id,
                )
            )

        logger.info(
            "[AGUI] stream completed thread_id=%s run_id=%s total_events=%s terminal=%s",
            thread_id,
            run_id,
            event_count,
            saw_terminal_event,
        )

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


@app.post("/invocations")
async def invocations(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid JSON body: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400, detail="Request body must be a JSON object"
        )

    return await _handle_agui(payload, request)


@app.get("/invocations/health")
async def invocations_health() -> dict[str, Any]:
    base_agent = cast(LangGraphAgent, app.state.agui_agent)
    return {"status": "ok", "agent": {"name": base_agent.name}}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
