"use client";

import { CopilotChat } from "@copilotkit/react-core/v2";
import { Canvas } from "@/components/canvas";
import { ExampleLayout } from "@/components/example-layout";
import { useExampleSuggestions, useGenerativeUIExamples } from "@/hooks";

export default function HomePage() {
  useGenerativeUIExamples();
  useExampleSuggestions();

  return (
    <main className="h-screen bg-gradient-to-br from-brand-teal/10 via-background to-brand-yellow/15">
      <ExampleLayout
        chatContent={
          <div className="h-full py-4">
            <CopilotChat className="h-full" agentId="langgraph-ag-ui-agent" />
          </div>
        }
        appContent={<Canvas />}
      />
    </main>
  );
}
