import base64
import json
import logging
import os
import traceback
from typing import Any

import boto3
import uvicorn
from ag_ui.core import RunAgentInput, RunErrorEvent, RunFinishedEvent
from ag_ui.encoder import EventEncoder
from ag_ui_strands import StrandsAgent
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from strands_code_interpreter import StrandsCodeInterpreterTools

from gateway.utils.gateway_access_token import get_gateway_access_token

logger = logging.getLogger("strands_single_agent")
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
logger.setLevel(logging.INFO)

app = FastAPI()

ACTOR_ID_KEYS = ("actor_id", "actorId", "user_id", "userId", "sub")


def get_ssm_parameter(parameter_name: str) -> str:
    """
    Fetch parameter from SSM Parameter Store.

    SSM Parameter Store is AWS's service for storing configuration values securely.
    This function retrieves values like Gateway URLs that are set during deployment.
    """
    region = os.environ.get(
        "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    )
    ssm = boto3.client("ssm", region_name=region)
    try:
        response = ssm.get_parameter(Name=parameter_name)
        return response["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        raise ValueError(f"SSM parameter not found: {parameter_name}")
    except Exception as e:
        raise ValueError(f"Failed to retrieve SSM parameter {parameter_name}: {e}")


def create_gateway_mcp_client(access_token: str) -> MCPClient:
    """
    Create MCP client for AgentCore Gateway with OAuth2 authentication.

    MCP (Model Context Protocol) is how agents communicate with tool providers.
    This creates a client that can talk to the AgentCore Gateway using the provided
    access token for authentication. The Gateway then provides access to Lambda-based tools.
    """
    stack_name = os.environ.get("STACK_NAME")
    if not stack_name:
        raise ValueError("STACK_NAME environment variable is required")

    # Validate stack name format to prevent injection
    if not stack_name.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Invalid STACK_NAME format")

    gateway_url = get_ssm_parameter(f"/{stack_name}/gateway_url")

    # Create MCP client with Bearer token authentication
    gateway_client = MCPClient(
        lambda: streamablehttp_client(
            url=gateway_url, headers={"Authorization": f"Bearer {access_token}"}
        ),
        prefix="gateway",
    )

    return gateway_client


def create_basic_agent(user_id: str, session_id: str) -> Agent:
    """
    Create a basic agent with Gateway MCP tools and memory integration.

    This function sets up an agent that can access tools through the AgentCore Gateway
    and maintains conversation memory. It handles authentication, creates the MCP client
    connection, and configures the agent with access to all tools available through
    the Gateway. If Gateway connection fails, it falls back to an agent without tools.
    """
    system_prompt = """You are a helpful assistant with access to tools via the Gateway and Code Interpreter.
    When asked about your tools, list them and explain what they do."""

    bedrock_model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0", temperature=0.1
    )

    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    # Configure AgentCore Memory
    agentcore_memory_config = AgentCoreMemoryConfig(
        memory_id=memory_id, session_id=session_id, actor_id=user_id
    )

    session_manager = AgentCoreMemorySessionManager(
        agentcore_memory_config=agentcore_memory_config,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    code_tools = StrandsCodeInterpreterTools(region)

    try:
        # Get OAuth2 access token and create Gateway MCP client
        access_token = get_gateway_access_token()

        # Create Gateway MCP client with authentication
        gateway_client = create_gateway_mcp_client(access_token)
        agent = Agent(
            name="BasicAgent",
            system_prompt=system_prompt,
            tools=[gateway_client, code_tools.execute_python_securely],
            model=bedrock_model,
            session_manager=session_manager,
            trace_attributes={
                "user.id": user_id,
                "session.id": session_id,
            },
        )
        return agent

    except Exception as e:
        logger.error("[AGENT ERROR] Error creating Gateway client: %s", e)
        traceback.print_exc()
        raise


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

    if not input_data.thread_id:
        raise HTTPException(
            status_code=400,
            detail="Missing threadId in AG-UI payload.",
        )

    session_id = input_data.thread_id

    logger.info(
        "[AGUI] received thread_id=%s run_id=%s actor_id=%s messages=%s tools=%s",
        thread_id,
        run_id,
        actor_id,
        len(getattr(input_data, "messages", []) or []),
        len(getattr(input_data, "tools", []) or []),
    )

    try:
        strands_core_agent = create_basic_agent(actor_id, session_id)
    except Exception as exc:
        logger.exception(
            "[AGUI] failed to initialize strands agent thread_id=%s run_id=%s",
            thread_id,
            run_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize Strands agent: {exc}",
        ) from exc

    request_agent = StrandsAgent(
        agent=strands_core_agent,
        name="StrandsSingleAgent",
        description="Strands single agent exposed via AG-UI",
    )
    request_agent._agents_by_thread[thread_id] = strands_core_agent  # type: ignore[attr-defined]

    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_generator():
        event_count = 0
        saw_terminal_event = False

        try:
            async for event in request_agent.run(input_data):
                event_count += 1
                event_type = _event_type(event)

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
            saw_terminal_event = True
            logger.exception(
                "[AGUI] stream failure thread_id=%s run_id=%s error=%s",
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
                "[AGUI] missing terminal event thread_id=%s run_id=%s total_events=%s",
                thread_id,
                run_id,
                event_count,
            )
            yield encoder.encode(
                RunFinishedEvent(
                    thread_id=thread_id,
                    run_id=run_id,
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
