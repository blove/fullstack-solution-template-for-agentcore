# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import json
from typing import Any

from ag_ui.core import RunAgentInput
from ag_ui_langgraph import LangGraphAgent

ACTOR_ID_KEYS = ("actor_id", "actorId", "user_id", "userId", "sub")


class ActorAwareLangGraphAgent(LangGraphAgent):
    """Preserve actor_id in checkpoint history lookups for AgentCore memory."""

    def _resolve_actor_id_from_config(self) -> str | None:
        if not isinstance(self.config, dict):
            return None

        configurable = self.config.get("configurable")
        if not isinstance(configurable, dict):
            return None

        for key in ACTOR_ID_KEYS:
            value = configurable.get(key)
            if isinstance(value, str) and value:
                return value

        return None

    async def get_checkpoint_before_message(self, message_id: str, thread_id: str):
        if not thread_id:
            raise ValueError("Missing thread_id in config")

        configurable: dict[str, Any] = {"thread_id": thread_id}
        actor_id = self._resolve_actor_id_from_config()
        if actor_id:
            configurable["actor_id"] = actor_id

        history_list = []
        async for snapshot in self.graph.aget_state_history({"configurable": configurable}):
            history_list.append(snapshot)

        history_list.reverse()
        for idx, snapshot in enumerate(history_list):
            messages = snapshot.values.get("messages", [])
            if any(getattr(message, "id", None) == message_id for message in messages):
                if idx == 0:
                    empty_snapshot = snapshot
                    empty_snapshot.values["messages"] = []
                    return empty_snapshot

                snapshot_values_without_messages = snapshot.values.copy()
                del snapshot_values_without_messages["messages"]
                checkpoint = history_list[idx - 1]

                merged_values = {**checkpoint.values, **snapshot_values_without_messages}
                return checkpoint._replace(values=merged_values)

        raise ValueError("Message ID not found in history")

    def set_message_in_progress(self, run_id: str, data: Any):
        """
        Guard against None placeholders from ag-ui internals during tool-call streaming.
        """
        current = self.messages_in_process.get(run_id)
        if not isinstance(current, dict):
            current = {}
        self.messages_in_process[run_id] = {**current, **data}

    def langgraph_default_merge_state(self, state: Any, messages: Any, input: RunAgentInput):
        """
        Bridge AG-UI's `tools` payload into CopilotKit's expected `copilotkit.actions`.
        """
        merged_state = super().langgraph_default_merge_state(state, messages, input)

        actions = merged_state.get("tools", [])
        context = input.context or []
        existing_copilotkit = merged_state.get("copilotkit", {})
        if not isinstance(existing_copilotkit, dict):
            existing_copilotkit = {}

        merged_state["copilotkit"] = {
            **existing_copilotkit,
            "actions": actions,
            "context": context,
        }
        return merged_state


def decode_jwt_sub(authorization_header: str | None) -> str | None:
    """Extract JWT sub claim from a Bearer token without validating signature."""
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


def resolve_actor_id(input_data: RunAgentInput, authorization_header: str | None) -> str | None:
    forwarded_props = (
        input_data.forwarded_props if isinstance(input_data.forwarded_props, dict) else {}
    )

    for key in ACTOR_ID_KEYS:
        value = forwarded_props.get(key)
        if isinstance(value, str) and value:
            return value

    return decode_jwt_sub(authorization_header)
