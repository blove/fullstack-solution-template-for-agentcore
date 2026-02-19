# LangGraph Single Agent Pattern

This pattern runs a LangGraph agent implementation for AgentCore runtime.

## Deploy (CDK)

Set the backend pattern in `infra-cdk/config.yaml`:

```yaml
backend:
  pattern: langgraph-single-agent
```

Then deploy with CDK as usual.

## Frontend

This pattern includes a full Next.js frontend in `patterns/langgraph-single-agent/frontend/`.
When `backend.pattern` is set to `langgraph-single-agent` in `infra-cdk/config.yaml`,
`python scripts/deploy-frontend.py` builds and deploys this pattern's frontend copy.
