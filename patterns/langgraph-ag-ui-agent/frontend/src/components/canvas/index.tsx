"use client";

import { useAgent } from "@copilotkit/react-core/v2";
import { TodoList } from "./todo-list";

interface Todo {
  id: string;
  title: string;
  description: string;
  emoji: string;
  status: "pending" | "completed";
}

export function Canvas() {
  const { agent } = useAgent({ agentId: "langgraph-ag-ui-agent" });
  const stateTodos = (agent.state as { todos?: Todo[] } | undefined)?.todos;
  const todos = Array.isArray(stateTodos) ? stateTodos : [];

  return (
    <div className="h-full p-8 bg-gray-50 dark:bg-black">
      <TodoList
        todos={todos}
        onUpdate={(updatedTodos) => agent.setState({ todos: updatedTodos })}
        isAgentRunning={agent.isRunning}
      />
    </div>
  );
}
