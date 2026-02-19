import {
  useFrontendTool,
  useHumanInTheLoop,
} from "@copilotkit/react-core/v2";
import { z } from "zod";
import { useDefaultTool } from "@copilotkit/react-core";
import { PieChart } from "@/components/generative-ui/charts/pie-chart";
import { BarChart } from "@/components/generative-ui/charts/bar-chart";
import { MeetingTimePicker } from "@/components/generative-ui/meeting-time-picker";

export const useGenerativeUIExamples = () => {
  useFrontendTool({
    name: "toggleTheme",
    description: "Toggle between light and dark mode for the app.",
    parameters: z.object({
      theme: z.enum(["light", "dark"]).describe("The theme to switch to"),
    }),
    handler: async ({ theme }) => {
      if (theme === "dark") {
        document.documentElement.classList.add("dark");
      } else {
        document.documentElement.classList.remove("dark");
      }
      return `Switched to ${theme} mode!`;
    },
  });

  useDefaultTool({
    render: ({ name, status }) => {
      const textStyles = "mt-2 text-sm text-gray-500";
      if (status !== "complete") {
        return <p className={textStyles}>Calling {name}...</p>;
      }
      return <p className={textStyles}>Called {name}!</p>;
    },
  });

  const chartSchema = z.object({
    title: z.string().describe("Chart title"),
    description: z.string().describe("Brief description or subtitle"),
    data: z
      .array(
        z.object({
          label: z.string(),
          value: z.number(),
        }),
      )
      .describe("Array of chart data points"),
  });

  useFrontendTool({
    name: "show_pie_chart",
    description: "Display data as a pie chart.",
    parameters: chartSchema,
    render: ({ args }) => {
      const { title, description, data } = args;
      const chartTitle = title || "Chart";
      const chartDescription = description || "";
      const chartData = (data as Array<{ label: string; value: number }>) || [];
      return <PieChart title={chartTitle} description={chartDescription} data={chartData} />;
    },
  });

  useFrontendTool({
    name: "show_bar_chart",
    description: "Display data as a bar chart.",
    parameters: chartSchema,
    render: ({ args }) => {
      const { title, description, data } = args;
      const chartTitle = title || "Chart";
      const chartDescription = description || "";
      const chartData = (data as Array<{ label: string; value: number }>) || [];
      return <BarChart title={chartTitle} description={chartDescription} data={chartData} />;
    },
  });

  useHumanInTheLoop({
    name: "demonstrateHumanInTheLoop",
    description: "Propose meeting times and ask the user to select one.",
    render: ({ respond, status }) => {
      return <MeetingTimePicker status={status} respond={respond} />;
    },
  });
};
