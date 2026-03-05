"""
CopilotKit Middleware for LangGraph agents.

Works with any agent (prebuilt or custom).

Example:
    from langgraph.prebuilt import create_agent
    from copilotkit import CopilotKitMiddleware

    agent = create_agent(
        model="openai:gpt-4o",
        tools=[backend_tool],
        middleware=[CopilotKitMiddleware()],
    )
"""

import json
import logging
from typing import Any, Callable, Awaitable, ClassVar, List, TypedDict

from langchain_core.messages import AIMessage, SystemMessage
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langgraph.runtime import Runtime

LOG = logging.getLogger(__name__)
PREFIX = "[CKMW]"


class CopilotContextItem(TypedDict):
    """Copilot context item"""
    description: str
    value: Any

class CopilotKitProperties(TypedDict):
    """CopilotKit state"""
    actions: List[Any]
    context: List[CopilotContextItem]
    intercepted_tool_calls: Any
    original_ai_message_id: Any

class StateSchema(AgentState):
    copilotkit: CopilotKitProperties


class CopilotKitMiddleware(AgentMiddleware[StateSchema, Any]):
    """CopilotKit Middleware for LangGraph agents.

    Handles frontend tool injection and interception for CopilotKit.
    """

    state_schema = StateSchema
    tools: ClassVar[list] = []

    @property
    def name(self) -> str:
        return "CopilotKitMiddleware"

    # Inject frontend tools before model call
    def wrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        frontend_tools = request.state.get("copilotkit", {}).get("actions", [])
        frontend_tool_names = [t.get("name") for t in frontend_tools] if frontend_tools else []
        backend_tool_names = [getattr(t, "name", str(t)) for t in request.tools] if request.tools else []

        LOG.info("%s wrap_model_call called", PREFIX)
        LOG.info("%s   backend_tools=%s", PREFIX, backend_tool_names)
        LOG.info("%s   frontend_tools=%s", PREFIX, frontend_tool_names)

        if not frontend_tools:
            LOG.info("%s   no frontend tools, passing through", PREFIX)
            return handler(request)

        # Merge frontend tools with existing tools
        merged_tools = [*request.tools, *frontend_tools]
        merged_names = [getattr(t, "name", None) or t.get("name", str(t)) for t in merged_tools]
        LOG.info("%s   merged_tools=%s", PREFIX, merged_names)

        return handler(request.override(tools=merged_tools))

    async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        frontend_tools = request.state.get("copilotkit", {}).get("actions", [])
        frontend_tool_names = [t.get("name") for t in frontend_tools] if frontend_tools else []
        backend_tool_names = [getattr(t, "name", str(t)) for t in request.tools] if request.tools else []

        LOG.info("%s awrap_model_call called", PREFIX)
        LOG.info("%s   backend_tools=%s", PREFIX, backend_tool_names)
        LOG.info("%s   frontend_tools=%s", PREFIX, frontend_tool_names)

        if not frontend_tools:
            LOG.info("%s   no frontend tools, passing through", PREFIX)
            return await handler(request)

        # Merge frontend tools with existing tools
        merged_tools = [*request.tools, *frontend_tools]
        merged_names = [getattr(t, "name", None) or t.get("name", str(t)) for t in merged_tools]
        LOG.info("%s   merged_tools=%s", PREFIX, merged_names)

        return await handler(request.override(tools=merged_tools))

    # Inject app context before agent runs
    def before_agent(
            self,
            state: StateSchema,
            runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        copilotkit_state = state.get("copilotkit", {})

        LOG.info("%s before_agent called", PREFIX)
        LOG.info("%s   message_count=%d", PREFIX, len(messages))
        LOG.info("%s   copilotkit_state_keys=%s", PREFIX, list(copilotkit_state.keys()) if isinstance(copilotkit_state, dict) else type(copilotkit_state))
        LOG.info("%s   actions_count=%d", PREFIX, len(copilotkit_state.get("actions", [])) if isinstance(copilotkit_state, dict) else 0)
        LOG.info("%s   context=%s", PREFIX, copilotkit_state.get("context") if isinstance(copilotkit_state, dict) else None)

        if not messages:
            LOG.info("%s   no messages, returning None", PREFIX)
            return None

        # Get app context from state or runtime
        app_context = copilotkit_state.get("context") or getattr(runtime, "context", None)

        # Check if app_context is missing or empty
        if not app_context:
            LOG.info("%s   no app_context, returning None", PREFIX)
            return None
        if isinstance(app_context, str) and app_context.strip() == "":
            LOG.info("%s   empty string app_context, returning None", PREFIX)
            return None
        if isinstance(app_context, dict) and len(app_context) == 0:
            LOG.info("%s   empty dict app_context, returning None", PREFIX)
            return None

        LOG.info("%s   injecting app context (type=%s)", PREFIX, type(app_context).__name__)

        # Create the context content
        if isinstance(app_context, str):
            context_content = app_context
        else:
            context_content = json.dumps(app_context, indent=2)

        context_message_content = f"App Context:\n{context_content}"
        context_message_prefix = "App Context:\n"

        # Helper to get message content as string
        def get_content_string(msg: Any) -> str | None:
            content = getattr(msg, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list) and content and isinstance(content[0], dict):
                return content[0].get("text")
            return None

        # Find the first system/developer message (not our context message)
        # to determine where to insert our context message (right after it)
        first_system_index = -1

        for i, msg in enumerate(messages):
            msg_type = getattr(msg, "type", None)
            if msg_type in ("system", "developer"):
                content = get_content_string(msg)
                # Skip if this is our own context message
                if content and content.startswith(context_message_prefix):
                    continue
                first_system_index = i
                break

        # Check if our context message already exists
        existing_context_index = -1
        for i, msg in enumerate(messages):
            msg_type = getattr(msg, "type", None)
            if msg_type in ("system", "developer"):
                content = get_content_string(msg)
                if content and content.startswith(context_message_prefix):
                    existing_context_index = i
                    break

        # Create the context message
        context_message = SystemMessage(content=context_message_content)

        if existing_context_index != -1:
            # Replace existing context message
            updated_messages = list(messages)
            updated_messages[existing_context_index] = context_message
            LOG.info("%s   replaced existing context message at index %d", PREFIX, existing_context_index)
        else:
            # Insert after the first system message, or at position 0 if no system message
            insert_index = first_system_index + 1 if first_system_index != -1 else 0
            updated_messages = [
                *messages[:insert_index],
                context_message,
                *messages[insert_index:],
            ]
            LOG.info("%s   inserted context message at index %d", PREFIX, insert_index)

        return {
            **state,
            "messages": updated_messages,
        }

    async def abefore_agent(
            self,
            state: StateSchema,
            runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        LOG.info("%s abefore_agent called (delegating to sync)", PREFIX)
        return self.before_agent(state, runtime)

    # Intercept frontend tool calls after model returns, before ToolNode executes
    def after_model(
            self,
            state: StateSchema,
            runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        LOG.info("%s after_model called", PREFIX)

        frontend_tools = state.get("copilotkit", {}).get("actions", [])
        if not frontend_tools:
            LOG.info("%s   no frontend tools in state, returning None", PREFIX)
            return None

        frontend_tool_names = {
            t.get("function", {}).get("name") or t.get("name")
            for t in frontend_tools
        }
        LOG.info("%s   frontend_tool_names=%s", PREFIX, frontend_tool_names)

        # Find last AI message with tool calls
        messages = state.get("messages", [])
        if not messages:
            LOG.info("%s   no messages, returning None", PREFIX)
            return None

        last_message = messages[-1]
        LOG.info("%s   last_message type=%s, id=%s", PREFIX, type(last_message).__name__, getattr(last_message, "id", None))

        if not isinstance(last_message, AIMessage):
            LOG.info("%s   last message is not AIMessage, returning None", PREFIX)
            return None

        tool_calls = getattr(last_message, "tool_calls", None) or []
        LOG.info("%s   tool_calls count=%d", PREFIX, len(tool_calls))
        for tc in tool_calls:
            LOG.info("%s   tool_call: name=%s id=%s", PREFIX, tc.get("name"), tc.get("id"))

        if not tool_calls:
            LOG.info("%s   no tool calls, returning None", PREFIX)
            return None

        backend_tool_calls = []
        frontend_tool_calls = []

        for call in tool_calls:
            if call.get("name") in frontend_tool_names:
                frontend_tool_calls.append(call)
            else:
                backend_tool_calls.append(call)

        LOG.info("%s   backend_tool_calls=%s", PREFIX, [c.get("name") for c in backend_tool_calls])
        LOG.info("%s   frontend_tool_calls=%s", PREFIX, [c.get("name") for c in frontend_tool_calls])

        if not frontend_tool_calls:
            LOG.info("%s   no frontend tool calls to intercept, returning None", PREFIX)
            return None

        # Create updated AIMessage with only backend tool calls
        updated_ai_message = AIMessage(
            content=last_message.content,
            tool_calls=backend_tool_calls,
            id=last_message.id,
        )

        result = {
            "messages": [*messages[:-1], updated_ai_message],
            "copilotkit": {
                "intercepted_tool_calls": frontend_tool_calls,
                "original_ai_message_id": last_message.id,
            },
        }
        LOG.info("%s   intercepted %d frontend tool calls, stored original_ai_message_id=%s", PREFIX, len(frontend_tool_calls), last_message.id)
        LOG.info("%s   updated AI message now has %d backend tool calls", PREFIX, len(backend_tool_calls))
        return result

    async def aafter_model(
            self,
            state: StateSchema,
            runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        LOG.info("%s aafter_model called (delegating to sync)", PREFIX)
        return self.after_model(state, runtime)

    # Restore frontend tool calls to AIMessage before agent exits
    def after_agent(
            self,
            state: StateSchema,
            runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        LOG.info("%s after_agent called", PREFIX)

        copilotkit_state = state.get("copilotkit", {})
        intercepted_tool_calls = copilotkit_state.get("intercepted_tool_calls")
        original_message_id = copilotkit_state.get("original_ai_message_id")

        LOG.info("%s   intercepted_tool_calls=%s", PREFIX, [c.get("name") for c in intercepted_tool_calls] if intercepted_tool_calls else None)
        LOG.info("%s   original_message_id=%s", PREFIX, original_message_id)

        if not intercepted_tool_calls or not original_message_id:
            LOG.info("%s   nothing to restore, returning None", PREFIX)
            return None

        messages = state.get("messages", [])
        LOG.info("%s   scanning %d messages for original AI message", PREFIX, len(messages))
        updated_messages = []
        restored = False

        for msg in messages:
            if isinstance(msg, AIMessage) and msg.id == original_message_id:
                existing_tool_calls = getattr(msg, "tool_calls", None) or []
                restored_calls = [*existing_tool_calls, *intercepted_tool_calls]
                updated_messages.append(AIMessage(
                    content=msg.content,
                    tool_calls=restored_calls,
                    id=msg.id,
                ))
                restored = True
                LOG.info("%s   restored %d intercepted tool calls to AI message %s (had %d existing)", PREFIX, len(intercepted_tool_calls), msg.id, len(existing_tool_calls))
                LOG.info("%s   final tool_calls on message: %s", PREFIX, [c.get("name") for c in restored_calls])
            else:
                updated_messages.append(msg)

        if not restored:
            LOG.warning("%s   FAILED to find AI message with id=%s to restore tool calls!", PREFIX, original_message_id)

        return {
            "messages": updated_messages,
            "copilotkit": {
                "intercepted_tool_calls": None,
                "original_ai_message_id": None,
            },
        }

    async def aafter_agent(
            self,
            state: StateSchema,
            runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        LOG.info("%s aafter_agent called (delegating to sync)", PREFIX)
        return self.after_agent(state, runtime)
