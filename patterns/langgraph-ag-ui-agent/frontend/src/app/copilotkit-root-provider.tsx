"use client";

import { CopilotKit } from "@copilotkit/react-core";
import { useAuth } from "@/hooks/useAuth";
import { useEffect, useState } from "react";
import "@copilotkit/react-core/v2/styles.css";

export default function CopilotKitRootProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const { token } = useAuth();
  const envRuntimeUrl = process.env.NEXT_PUBLIC_COPILOTKIT_RUNTIME_URL ?? "";
  const [runtimeUrl, setRuntimeUrl] = useState<string>(envRuntimeUrl);
  const [runtimeLoadFailed, setRuntimeLoadFailed] = useState(false);

  const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
  const resolvedRuntimeUrl = runtimeUrl || envRuntimeUrl;

  useEffect(() => {
    let isMounted = true;

    async function loadRuntimeUrlFromConfig() {
      try {
        const response = await fetch("/aws-exports.json");
        if (!response.ok) {
          throw new Error(`Failed to load aws-exports.json: ${response.status}`);
        }

        const config = (await response.json()) as { copilotKitRuntimeUrl?: string };
        if (isMounted && config.copilotKitRuntimeUrl) {
          setRuntimeUrl(config.copilotKitRuntimeUrl);
          setRuntimeLoadFailed(false);
        }
      } catch (error) {
        console.error("Failed to resolve copilotKitRuntimeUrl:", error);
        if (isMounted && !envRuntimeUrl) {
          setRuntimeLoadFailed(true);
        }
      }
    }

    void loadRuntimeUrlFromConfig();

    return () => {
      isMounted = false;
    };
  }, [envRuntimeUrl]);

  if (!resolvedRuntimeUrl) {
    return (
      <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center px-6 text-center">
        <h1 className="text-2xl font-semibold text-brand-dark">CopilotKit runtime not configured</h1>
        <p className="mt-3 text-sm text-muted-foreground">
          {runtimeLoadFailed
            ? "Set copilotKitRuntimeUrl in aws-exports.json or NEXT_PUBLIC_COPILOTKIT_RUNTIME_URL."
            : "Loading CopilotKit runtime configuration..."}
        </p>
      </main>
    );
  }

  return (
    <CopilotKit
      runtimeUrl={resolvedRuntimeUrl}
      agent="langgraph-ag-ui-agent"
      headers={headers}
    >
      {children}
    </CopilotKit>
  );
}
