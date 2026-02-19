"use client";

import { ReactNode, useState } from "react";
import { ModeToggle } from "./mode-toggle";
import { useFrontendTool } from "@copilotkit/react-core/v2";

interface ExampleLayoutProps {
  chatContent: ReactNode;
  appContent: ReactNode;
}

export function ExampleLayout({
  chatContent,
  appContent,
}: ExampleLayoutProps) {
  const [mode, setMode] = useState<"chat" | "app">("chat");

  useFrontendTool({
    name: "enableAppMode",
    description: "Enable app mode when working with the todo canvas.",
    handler: async () => {
      setMode("app");
    },
  });

  useFrontendTool({
    name: "enableChatMode",
    description: "Enable chat mode",
    handler: async () => {
      setMode("chat");
    },
  });

  return (
    <div className="flex h-full flex-row">
      <ModeToggle mode={mode} onModeChange={setMode} />
      <div
        className={`max-h-full overflow-y-auto ${
          mode === "app"
            ? "w-1/3 px-6 max-lg:hidden"
            : "flex-1 px-4 lg:px-6"
        }`}
      >
        {chatContent}
      </div>
      <div
        className={`h-full overflow-hidden ${
          mode === "app"
            ? "w-2/3 border-l dark:border-zinc-700 max-lg:w-full max-lg:border-l-0"
            : "w-0 border-l-0"
        }`}
      >
        <div className="h-full w-full lg:w-[66.666vw]">{appContent}</div>
      </div>
    </div>
  );
}
