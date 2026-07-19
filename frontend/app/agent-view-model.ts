export interface AgentStep {
  round: number;
  status: string;
  tool_name?: string | null;
  tool_args?: Record<string, unknown>;
  tool_status?: string;
}

export interface AgentRunResponse {
  answer: string;
  sources: Array<Record<string, unknown>>;
  selected_tool: string;
  tool_status: string;
  termination_reason: string;
  steps: AgentStep[];
}

export interface AgentStepViewModel {
  roundLabel: string;
  statusLabel: string;
  toolLabel: string;
  argsLabel: string | null;
  toolStatusLabel: string | null;
}

function readableStatus(status: string): string {
  return status.replaceAll("_", " ");
}

export function buildAgentStepViewModel(
  step: AgentStep,
): AgentStepViewModel {
  const hasToolArgs = step.tool_args && Object.keys(step.tool_args).length > 0;

  return {
    roundLabel: `第 ${step.round} 轮`,
    statusLabel: readableStatus(step.status),
    toolLabel: step.tool_name || "final answer",
    argsLabel: hasToolArgs ? JSON.stringify(step.tool_args) : null,
    toolStatusLabel: step.tool_status
      ? readableStatus(step.tool_status)
      : null,
  };
}
