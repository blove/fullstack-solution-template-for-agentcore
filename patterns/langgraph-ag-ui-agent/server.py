# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import logging
import traceback
from typing import cast

import uvicorn
from ag_ui.core import (
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.actor import ActorAwareLangGraphAgent, resolve_actor_id
from src.agent import build_langgraph_agent

app = FastAPI()
logger = logging.getLogger("langgraph_ag_ui_agent.server")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
logger.setLevel(logging.INFO)


def _event_type(event: object) -> str:
    if isinstance(event, dict):
        event_type = event.get("type")
        if hasattr(event_type, "value"):
            return str(event_type.value)
        return str(event_type) if event_type else "UNKNOWN"

    event_type = getattr(event, "type", None)
    if hasattr(event_type, "value"):
        return str(event_type.value)
    return str(event_type) if event_type else "UNKNOWN"


def _normalize_event(event: object) -> object:
    if not isinstance(event, dict):
        return event

    event_type = event.get("type")
    if hasattr(event_type, "value"):
        return {**event, "type": str(event_type.value)}

    return event


@app.on_event("startup")
async def startup_event() -> None:
    try:
        app.state.agent = build_langgraph_agent()
        logger.info("[AGENT] AG-UI endpoint mounted at POST /invocations")
    except Exception as exc:
        logger.error("[AGENT ERROR] Failed to start agent: %s", exc)
        traceback.print_exc()
        raise


@app.get("/ping")
async def ping() -> dict[str, str]:
    return {"status": "Healthy"}


@app.post("/invocations")
async def langgraph_agent_endpoint(input_data: RunAgentInput, request: Request):
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
        "[RUN] received thread_id=%s run_id=%s actor_id=%s messages=%s tools=%s",
        thread_id,
        run_id,
        actor_id,
        len(getattr(input_data, "messages", []) or []),
        len(getattr(input_data, "tools", []) or []),
    )

    base_agent = cast(ActorAwareLangGraphAgent, app.state.agent)
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
                event = _normalize_event(event)
                event_type = _event_type(event)

                if event_type in {
                    "RUN_STARTED",
                    "RUN_FINISHED",
                    "RUN_ERROR",
                    "TOOL_CALL_START",
                    "TOOL_CALL_RESULT",
                }:
                    logger.info(
                        "[RUN] event thread_id=%s run_id=%s event=%s count=%s",
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
                    "[RUN] stream cancelled thread_id=%s run_id=%s",
                    thread_id,
                    run_id,
                )
                return
            saw_terminal_event = True
            logger.exception(
                "[RUN] stream failure thread_id=%s run_id=%s error=%s",
                thread_id,
                run_id,
                exc,
            )
            yield encoder.encode(
                RunErrorEvent(
                    message=str(exc) or type(exc).__name__,
                    code=type(exc).__name__,
                )
            )

        if not saw_terminal_event:
            logger.error(
                "[RUN] missing terminal event thread_id=%s run_id=%s total_events=%s",
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
            "[RUN] stream completed thread_id=%s run_id=%s total_events=%s terminal=%s",
            thread_id,
            run_id,
            event_count,
            saw_terminal_event,
        )

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


@app.get("/invocations/health")
async def invocations_health():
    base_agent = cast(ActorAwareLangGraphAgent, app.state.agent)
    return {"status": "ok", "agent": {"name": base_agent.name}}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
