# LangGraph AG-UI Agent Pattern

This pattern runs a LangGraph agent directly behind the AG-UI protocol using a single FastAPI server.
The AG-UI endpoint is mounted at **POST `/invocations`** (required for Bedrock AgentCore runtime
invocation). A simple **GET `/ping`** endpoint is included for health checks.

## Local Run

From the repository root:

```bash
export MEMORY_ID=your-memory-id
export STACK_NAME=your-stack-name
export AWS_DEFAULT_REGION=us-east-1

uv run patterns/langgraph-ag-ui-agent/server.py
```

Then test:

- `GET http://localhost:8080/ping`
- `POST http://localhost:8080/invocations` (AG-UI protocol)

## Deploy (CDK)

Set the backend pattern in `infra-cdk/config.yaml`:

```yaml
backend:
  pattern: langgraph-ag-ui-agent
```

Then deploy with CDK as usual.

## Frontend

This pattern includes a full Next.js frontend in `patterns/langgraph-ag-ui-agent/frontend/`.
When `backend.pattern` is set to `langgraph-ag-ui-agent` in `infra-cdk/config.yaml`,
`python scripts/deploy-frontend.py` builds and deploys this pattern's frontend copy.

## Notes

- The AG-UI endpoint is **POST `/invocations`** and is the only invocation route used by this pattern.
- The agent uses Bedrock (`ChatBedrock`), Gateway MCP tools, and AgentCore memory.
