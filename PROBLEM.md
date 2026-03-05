# Problem: Frontend tool calls interrupted before completion

## How to reproduce

Run `bash curl_cmd.sh` — this sends a POST to the CopilotKit runtime API asking:
"Please show me the distribution of our revenue by category in a pie chart."

## What happens

The LangGraph agent processes the request in two steps:

1. **`query_data` (backend tool)** — succeeds. The agent calls this to fetch revenue data from the CSV database. Returns full dataset with chart-ready data.

2. **`show_pie_chart` (frontend tool)** — fails. The agent calls this CopilotKit frontend action to render the pie chart on the client. Instead of being passed through to the frontend for execution, it gets interrupted.

## The error

In the `MESSAGES_SNAPSHOT` event (sent just before `RUN_FINISHED`), the last message is:

```json
{
  "id": "None",
  "content": "Tool call 'show_pie_chart' with id 'toolu_bdrk_01XbJshAzp7kqRWgFtBsPdhY' was interrupted before completion.",
  "role": "tool",
  "toolCallId": "toolu_bdrk_01XbJshAzp7kqRWgFtBsPdhY",
  "status": "error"
}
```

## Expected behavior

Frontend tool calls like `show_pie_chart` should be **intercepted** by `CopilotKitMiddleware.after_model()`, stored as `intercepted_tool_calls`, and then **restored** by `after_agent()` so they flow back to the CopilotKit frontend for client-side rendering. The tool call should never reach the LangGraph `ToolNode` for execution.

## Actual behavior

The frontend tool call is not being properly intercepted. It appears to reach the ToolNode, which cannot execute it (since it's a frontend-only tool), resulting in the "interrupted before completion" error message.

## Key files

- `patterns/langgraph-single-agent/langgraph_agent.py` — agent entry point, uses `CopilotKitMiddleware`
- `patterns/langgraph-single-agent/copilotkit_lg_middleware.py` — local copy of the middleware (from CopilotKit repo) for debugging
- `infra-cdk/lambdas/copilotkit-runtime/src/index.ts` — CopilotKit runtime Lambda that bridges frontend to AgentCore
