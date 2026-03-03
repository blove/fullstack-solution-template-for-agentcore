import {
  useDefaultRenderTool,
  useFrontendTool,
  useHumanInTheLoop,
} from "@copilotkit/react-core/v2";
import { z } from "zod";
import { PieChart } from "@/components/generative-ui/charts/pie-chart";
import { BarChart } from "@/components/generative-ui/charts/bar-chart";
import { MeetingTimePicker } from "@/components/generative-ui/meeting-time-picker";

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

type ChartData = Array<{ label: string; value: number }>;

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
  }, []);

  useDefaultRenderTool({
    render: ({ name, status }) => {
      const textStyles = "mt-2 text-sm text-gray-500";
      if (status !== "complete") {
        return <p className={textStyles}>Calling {name}...</p>;
      }
      return <p className={textStyles}>Called {name}!</p>;
    },
  }, []);

  useFrontendTool({
    name: "show_pie_chart",
    description: "Display data as a pie chart.",
    parameters: chartSchema,
    render: ({ args }) => {
      const { title, description, data } = args;
      const chartTitle = title || "Chart";
      const chartDescription = description || "";
      const chartData = (data as ChartData) || [];
      return <PieChart title={chartTitle} description={chartDescription} data={chartData} />;
    },
  }, []);

  useFrontendTool({
    name: "show_bar_chart",
    description: "Display data as a bar chart.",
    parameters: chartSchema,
    render: ({ args }) => {
      const { title, description, data } = args;
      const chartTitle = title || "Chart";
      const chartDescription = description || "";
      const chartData = (data as ChartData) || [];
      return <BarChart title={chartTitle} description={chartDescription} data={chartData} />;
    },
  }, []);

  useHumanInTheLoop({
    name: "scheduleTime",
    description: "Use human-in-the-loop to schedule a meeting with the user.",
    parameters: z.object({
      reasonForScheduling: z.string().describe("Reason for scheduling, very brief - 5 words."),
      meetingDuration: z.number().describe("Duration of the meeting in minutes"),
    }),
    render: ({ respond, status, args }) => {
      console.info("[HITL DEBUG] scheduleTime render", {
        status,
        hasRespond: typeof respond === "function",
        args,
      });
      return <MeetingTimePicker status={status} respond={respond} {...args} />;
    },
  }, []);
};
