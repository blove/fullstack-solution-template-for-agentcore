"use client"

import CopilotKitRootProvider from "@/app/copilotkit-root-provider"
import { Canvas } from "@/components/canvas"
import { ExampleLayout } from "@/components/example-layout"
import { Button } from "@/components/ui/button"
import { useExampleSuggestions, useGenerativeUIExamples } from "@/hooks"
import { useAuth } from "@/hooks/useAuth"
import { CopilotChat } from "@copilotkit/react-core/v2"

const COPILOTKIT_AGENT_ID = process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_ID ?? "langgraph-single-agent"

function CopilotExperience() {
  useGenerativeUIExamples()
  useExampleSuggestions()

  return (
    <main className="h-screen bg-gradient-to-br from-brand-teal/10 via-background to-brand-yellow/15">
      <ExampleLayout
        chatContent={
          <div className="h-full py-4">
            <CopilotChat className="h-full" agentId={COPILOTKIT_AGENT_ID} />
          </div>
        }
        appContent={<Canvas />}
      />
    </main>
  )
}

export default function ChatPage() {
  const { isAuthenticated, signIn } = useAuth()

  if (!isAuthenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-4">
        <p className="text-4xl">Please sign in</p>
        <Button onClick={() => signIn()}>Sign In</Button>
      </div>
    )
  }

  return (
    <CopilotKitRootProvider>
      <CopilotExperience />
    </CopilotKitRootProvider>
  )
}
