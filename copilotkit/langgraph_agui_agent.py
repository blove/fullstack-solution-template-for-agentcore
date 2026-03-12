import json
import logging
from typing import Dict, Any, List, Optional, Union, AsyncGenerator
from enum import Enum
from ag_ui_langgraph import LangGraphAgent
from langchain_core.messages import AIMessage, ToolMessage
from ag_ui.core import (
    EventType,
    CustomEvent,
    TextMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    ToolCallStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    StateSnapshotEvent,
)
from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig

try:
    from langchain.schema import BaseMessage
except ImportError:
    # Langchain >= 1.0.0
    from langchain_core.messages import BaseMessage


class CustomEventNames(Enum):
    """Custom event names for CopilotKit"""
    ManuallyEmitMessage = "copilotkit_manually_emit_message"
    ManuallyEmitToolCall = "copilotkit_manually_emit_tool_call"
    ManuallyEmitState = "copilotkit_manually_emit_intermediate_state"


class LangGraphEventTypes(Enum):
    """LangGraph event types"""
    OnChatModelStream = "on_chat_model_stream"
    OnCustomEvent = "on_custom_event"


class PredictStateTool:
    def __init__(self, tool: str, state_key: str, tool_argument: str):
        self.tool = tool
        self.state_key = state_key
        self.tool_argument = tool_argument


State = Dict[str, Any]
SchemaKeys = Dict[str, List[str]]
TextMessageEvents = Union[TextMessageStartEvent, TextMessageContentEvent, TextMessageEndEvent]
ToolCallEvents = Union[ToolCallStartEvent, ToolCallArgsEvent, ToolCallEndEvent]


class LangGraphAGUIAgent(LangGraphAgent):
    def __init__(self, *, name: str, graph: CompiledStateGraph, description: Optional[str] = None, config: Union[Optional[RunnableConfig], dict] = None):
        super().__init__(name=name, graph=graph, description=description, config=config)
        self.constant_schema_keys = self.constant_schema_keys + ["copilotkit"]

    def _dispatch_event(self, event) -> str:
        """Override the dispatch event method to handle custom CopilotKit events and filtering.

        Note: Returns None for filtered events (which violates the str return type annotation,
        but the base class also violates it by returning event objects). The None values are
        filtered out in run() before reaching the encoder.
        """
        
        if event.type == EventType.CUSTOM:
            custom_event = event

            if custom_event.name == CustomEventNames.ManuallyEmitMessage.value:
                # Emit the message events
                super()._dispatch_event(
                    TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        role="assistant",
                        message_id=custom_event.value["message_id"],
                        raw_event=event,
                    )
                )
                super()._dispatch_event(
                    TextMessageContentEvent(
                        type=EventType.TEXT_MESSAGE_CONTENT,
                        message_id=custom_event.value["message_id"],
                        delta=custom_event.value["message"],
                        raw_event=event,
                    )
                )
                super()._dispatch_event(
                    TextMessageEndEvent(
                        type=EventType.TEXT_MESSAGE_END,
                        message_id=custom_event.value["message_id"],
                        raw_event=event,
                    )
                )
                return super()._dispatch_event(event)

            if custom_event.name == CustomEventNames.ManuallyEmitToolCall.value:
                # Emit the tool call events
                super()._dispatch_event(
                    ToolCallStartEvent(
                        type=EventType.TOOL_CALL_START,
                        tool_call_id=custom_event.value["id"],
                        tool_call_name=custom_event.value["name"],
                        parent_message_id=custom_event.value["id"],
                        raw_event=event,
                    )
                )
                super()._dispatch_event(
                    ToolCallArgsEvent(
                        type=EventType.TOOL_CALL_ARGS,
                        tool_call_id=custom_event.value["id"],
                        delta=custom_event.value["args"] if isinstance(custom_event.value["args"], str) else json.dumps(
                            custom_event.value["args"]),
                        raw_event=event,
                    )
                )
                super()._dispatch_event(
                    ToolCallEndEvent(
                        type=EventType.TOOL_CALL_END,
                        tool_call_id=custom_event.value["id"],
                        raw_event=event,
                    )
                )
                return super()._dispatch_event(event)

            if custom_event.name == CustomEventNames.ManuallyEmitState.value:
                self.active_run["manually_emitted_state"] = custom_event.value
                return super()._dispatch_event(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot=self.get_state_snapshot(self.active_run["manually_emitted_state"]),
                        raw_event=event,
                    )
                )

            if custom_event.name == "copilotkit_exit":
                return super()._dispatch_event(
                    CustomEvent(
                        type=EventType.CUSTOM,
                        name="Exit",
                        value=True,
                        raw_event=event,
                    )
                )

        # Handle filtering based on metadata for text messages and tool calls
        raw_event = getattr(event, 'raw_event', None)
        if raw_event:
            is_message_event = event.type in [
                EventType.TEXT_MESSAGE_START,
                EventType.TEXT_MESSAGE_CONTENT,
                EventType.TEXT_MESSAGE_END
            ]
            is_tool_event = event.type in [
                EventType.TOOL_CALL_START,
                EventType.TOOL_CALL_ARGS,
                EventType.TOOL_CALL_END
            ]

            # Handle both dict and object cases for raw_event
            # See: https://github.com/CopilotKit/CopilotKit/issues/2066
            metadata = (raw_event.get('metadata', {}) if isinstance(raw_event, dict)
                        else getattr(raw_event, 'metadata', {})) or {}

            if "copilotkit:emit-tool-calls" in metadata:
                if metadata["copilotkit:emit-tool-calls"] is False and is_tool_event:
                    return None  # Don't dispatch this event

            if "copilotkit:emit-messages" in metadata:
                if metadata["copilotkit:emit-messages"] is False and is_message_event:
                    return None  # Don't dispatch this event

        return super()._dispatch_event(event)

    async def run(self, input):
        """Override run to filter out None events from _dispatch_event filtering."""
        async for event in super().run(input):
            if event is not None:
                yield event

    async def _handle_single_event(self, event: Any, state: State) -> AsyncGenerator[str, None]:
        """Override to add custom event processing for PredictState events"""
        
        # First, check if this is a raw event that should generate a PredictState event
        if event.get("event") == LangGraphEventTypes.OnChatModelStream.value:
            predict_state_metadata = event.get("metadata", {}).get("copilotkit:emit-intermediate-state", None)
            if predict_state_metadata is not None:
                event["metadata"]['predict_state'] = predict_state_metadata

        # Call the parent method to handle all other events
        async for event_str in super()._handle_single_event(event, state):
            yield event_str

    def langgraph_default_merge_state(self, state: State, messages: List[BaseMessage], input: Any) -> State:
        """Override to add CopilotKit actions and fix ToolMessage ordering for Bedrock."""
        _log = logging.getLogger("copilotkit.merge")

        # state["messages"] has the existing checkpoint messages.
        # super() returns {"messages": new_messages, ...} — only messages not in existing.
        existing_messages = state.get("messages", [])
        merged_state = super().langgraph_default_merge_state(state, messages, input)
        new_messages = merged_state.get("messages", [])

        _log.info("[MERGE] existing=%d, new=%d", len(existing_messages), len(new_messages))
        for m in new_messages:
            _log.info("[MERGE]   new: %s id=%s tc=%s", type(m).__name__, m.id,
                      getattr(m, "tool_call_id", None) or getattr(m, "tool_calls", None))

        # Collect new ToolMessages that belong to tool_calls in existing AIMessages.
        # These must be inserted right after their parent AIMessage (Bedrock requires it).
        pending = {m.tool_call_id: m for m in new_messages if isinstance(m, ToolMessage)}
        if pending:
            all_existing_tc_ids = set()
            for msg in existing_messages:
                if isinstance(msg, AIMessage):
                    for tc in (msg.tool_calls or []):
                        all_existing_tc_ids.add(tc["id"])

            # Walk existing messages backwards, insert matching ToolMessages after parent AI
            for i in range(len(existing_messages) - 1, -1, -1):
                if isinstance(existing_messages[i], AIMessage):
                    for tc in reversed(existing_messages[i].tool_calls or []):
                        if tc["id"] in pending:
                            _log.info("[MERGE] inserting ToolMessage for tc=%s after existing[%d]", tc["id"], i)
                            existing_messages.insert(i + 1, pending.pop(tc["id"]))

            # Remove inserted ToolMessages from new_messages (they're now in existing)
            new_messages = [m for m in new_messages
                           if not (isinstance(m, ToolMessage) and m.tool_call_id not in pending)]
            merged_state = {**merged_state, "messages": new_messages}

        _log.info("[MERGE] result: new_messages=%d, existing=%d", len(new_messages), len(existing_messages))

        # Extract tools from the merged state and add them as CopilotKit actions
        agui_properties = merged_state.get('ag-ui', {}) or merged_state

        return {
            **merged_state,
            'copilotkit': {
                'actions': agui_properties.get('tools', []),
                'context': agui_properties.get('context', [])
            },
        }

    def dict_repr(self):
        """Return dictionary representation of the agent"""
        super_repr = super().dict_repr()
        return {
            **super_repr,
            'type': 'langgraph_agui'
        }
