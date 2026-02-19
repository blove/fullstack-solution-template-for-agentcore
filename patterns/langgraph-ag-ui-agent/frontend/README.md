# LangGraph AG-UI Agent Frontend

This is the Next.js frontend for the `langgraph-ag-ui-agent` pattern.
It is wired to CopilotKit v2 and the backend agent id `langgraph-ag-ui-agent`.

## What This Frontend Includes

- `CopilotChat` as the main chat UI (`src/app/page.tsx`)
- Always-on prompt suggestions (`src/hooks/use-example-suggestions.tsx`)
- Controlled generative UI tools:
  - `show_pie_chart`
  - `show_bar_chart`
  - `toggleTheme`
  - `demonstrateHumanInTheLoop`
- Shared-state todo canvas using `useAgent` and `agent.setState(...)`
- Chat/App mode switching via frontend tools:
  - `enableAppMode`
  - `enableChatMode`

## Local Development

### Prerequisites

- Node.js 20+
- npm

### Start

```bash
cd patterns/langgraph-ag-ui-agent/frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Runtime And Auth Configuration

The app reads config from `public/aws-exports.json` and supports env var overrides.

### Runtime URL Resolution

`CopilotKitRootProvider` resolves runtime in this order:

1. `NEXT_PUBLIC_COPILOTKIT_RUNTIME_URL`
2. `copilotKitRuntimeUrl` from `public/aws-exports.json`

If neither is set, the app renders a runtime configuration error screen.

### Cognito/OIDC Resolution

Auth config is loaded from `public/aws-exports.json`, with these optional overrides:

- `NEXT_PUBLIC_COGNITO_USER_POOL_ID`
- `NEXT_PUBLIC_COGNITO_CLIENT_ID`
- `NEXT_PUBLIC_COGNITO_REGION`
- `NEXT_PUBLIC_COGNITO_REDIRECT_URI`
- `NEXT_PUBLIC_COGNITO_POST_LOGOUT_REDIRECT_URI`
- `NEXT_PUBLIC_COGNITO_RESPONSE_TYPE`
- `NEXT_PUBLIC_COGNITO_SCOPE`
- `NEXT_PUBLIC_COGNITO_AUTOMATIC_SILENT_RENEW`

`AuthProvider` is expected to remain enabled in `src/app/layout.tsx` for this pattern.

### Required Agent Id

This frontend is hard-wired to:

- `agent="langgraph-ag-ui-agent"` in `CopilotKit`
- `agentId="langgraph-ag-ui-agent"` in `CopilotChat`
- `useAgent({ agentId: "langgraph-ag-ui-agent" })` in the canvas

The runtime must expose that exact agent id.

## Expected Backend Tool Contract

The frontend registers and expects these tool names:

- `enableAppMode`
- `enableChatMode`
- `toggleTheme`
- `show_pie_chart`
- `show_bar_chart`
- `demonstrateHumanInTheLoop`

For shared todo state, the backend should maintain `state.todos` with:

```ts
{
  id: string;
  title: string;
  description: string;
  emoji: string;
  status: "pending" | "completed";
}
```

## Scripts

- `npm run dev` - start local dev server (Turbopack)
- `npm run lint` - run ESLint on `src/`
- `npm run build` - create static export in `build/`
- `npm run start` - run Next.js start command
- `npm run clean` - remove build artifacts and `node_modules`

## Deployment Notes

- Frontend deploy is handled by:
  - `python /Users/blove/repos/fullstack-solution-template-for-agentcore/scripts/deploy-frontend.py`
- The deploy script generates `public/aws-exports.json` from CDK outputs and copies it into the exported `build/` assets.

For full stack deploy steps, see:

- [Top-level README](../../../README.md)
- [Deployment docs](../../../docs/DEPLOYMENT.md)
