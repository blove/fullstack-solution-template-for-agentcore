import { useConfigureSuggestions } from "@copilotkit/react-core/v2";

export const useExampleSuggestions = () => {
  useConfigureSuggestions({
    suggestions: [
      {
        title: "Pie chart (Controlled UI)",
        message: "Please show me the distribution of our revenue by category in a pie chart.",
      },
      {
        title: "Bar chart (Controlled UI)",
        message: "Please show me the distribution of our expenses by category in a bar chart.",
      },
      {
        title: "Theme change (Frontend tool)",
        message: "Switch the app to dark mode.",
      },
      {
        title: "Canvas (Shared state)",
        message: "Open app mode and add todos for learning CopilotKit and AG-UI.",
      },
      {
        title: "Scheduling (HITL)",
        message: "Please demonstrate frontend-based human-in-the-loop by asking me to pick a meeting time.",
      },
    ],
    available: "always",
  });
};
