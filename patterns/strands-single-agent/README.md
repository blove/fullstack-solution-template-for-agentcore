# Strands Single Agent Pattern

This pattern runs a Strands-based single agent implementation for AgentCore runtime.

## Deploy (CDK)

Set the backend pattern in `infra-cdk/config.yaml`:

```yaml
backend:
  pattern: strands-single-agent
```

Then deploy with CDK as usual.

## Frontend

This pattern includes a full Next.js frontend in `patterns/strands-single-agent/frontend/`.
When `backend.pattern` is set to `strands-single-agent` in `infra-cdk/config.yaml`,
`python scripts/deploy-frontend.py` builds and deploys this pattern's frontend copy.
