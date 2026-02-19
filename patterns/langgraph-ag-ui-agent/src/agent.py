# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os

from copilotkit import CopilotKitMiddleware
from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from src.actor import ActorAwareLangGraphAgent
from src.prompts import SYSTEM_PROMPT
from src.query import query_data
from src.todos import AgentState, todo_tools


def build_langgraph_agent() -> ActorAwareLangGraphAgent:
    model_id = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    print(f"[AGENT] Using Bedrock model: {model_id}")

    bedrock_model = ChatBedrock(
        model_id=model_id,
        temperature=0.1,
        streaming=False,
    )

    memory_id = os.environ.get("MEMORY_ID")
    if not memory_id:
        raise ValueError("MEMORY_ID environment variable is required")

    checkpointer = AgentCoreMemorySaver(
        memory_id=memory_id,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )

    graph = create_agent(
        model=bedrock_model,
        tools=[query_data, *todo_tools],
        checkpointer=checkpointer,
        middleware=[CopilotKitMiddleware()],
        state_schema=AgentState,
        system_prompt=SYSTEM_PROMPT,
    )

    return ActorAwareLangGraphAgent(
        name="LangGraphAGUIAgent",
        description="LangGraph agent exposed via AG-UI",
        graph=graph,
    )
