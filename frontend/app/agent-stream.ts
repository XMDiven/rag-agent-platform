import type {AgentStep} from "./agent-view-model";

interface AnswerDeltaEvent {
  version: 1;
  type: "answer_delta";
  data: {text: string};
}

interface StepEvent {
  version: 1;
  type: "step";
  data: AgentStep;
}

interface SourcesEvent {
  version: 1;
  type: "sources";
  data: {sources: Array<Record<string, unknown>>};
}

interface ErrorEvent {
  version: 1;
  type: "error";
  data: {code: string; message: string};
}

interface DoneEvent {
  version: 1;
  type: "done";
  data: {
    termination_reason: string;
    selected_tool: string;
    tool_status: string;
  };
}

export type AgentStreamEvent =
  | AnswerDeltaEvent
  | StepEvent
  | SourcesEvent
  | ErrorEvent
  | DoneEvent;

export interface AgentStreamState {
  answer: string;
  steps: AgentStep[];
  sources: Array<Record<string, unknown>>;
  terminationReason: string;
  selectedTool: string;
  toolStatus: string;
  error: string;
  completed: boolean;
}

export class AgentStreamProtocolError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "AgentStreamProtocolError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalidEvent(): never {
  throw new AgentStreamProtocolError("invalid_event", "收到无效的流事件");
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function isOneOf(value: unknown, allowed: readonly string[]): value is string {
  return typeof value === "string" && allowed.includes(value);
}

export function parseAgentEvent(line: string): AgentStreamEvent {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new AgentStreamProtocolError("invalid_json", "收到无效的流数据");
  }

  if (!isRecord(value)) invalidEvent();
  if (value.version !== 1) {
    throw new AgentStreamProtocolError(
      "unsupported_version",
      "不支持的流协议版本",
    );
  }
  if (!isRecord(value.data)) invalidEvent();

  const data = value.data;
  switch (value.type) {
    case "answer_delta":
      if (!isNonEmptyString(data.text)) invalidEvent();
      return value as unknown as AnswerDeltaEvent;
    case "step":
      if (
        typeof data.round !== "number" ||
        !Number.isInteger(data.round) ||
        data.round < 1 ||
        !isOneOf(data.status, ["tool_executed", "tool_failed"]) ||
        typeof data.tool_name !== "string" ||
        !isRecord(data.tool_args) ||
        !isOneOf(data.tool_status, ["success", "failed"])
      ) {
        invalidEvent();
      }
      return value as unknown as StepEvent;
    case "sources":
      if (
        !Array.isArray(data.sources) ||
        !data.sources.every((source) => isRecord(source))
      ) {
        invalidEvent();
      }
      return value as unknown as SourcesEvent;
    case "error":
      if (
        !isOneOf(data.code, [
          "agent_stream_failed",
          "mixed_model_output",
          "empty_model_output",
        ]) ||
        !isNonEmptyString(data.message)
      ) {
        invalidEvent();
      }
      return value as unknown as ErrorEvent;
    case "done":
      if (
        !isOneOf(data.termination_reason, [
          "final_answer",
          "max_steps",
          "failed",
          "single_step",
        ]) ||
        typeof data.selected_tool !== "string" ||
        !isOneOf(data.tool_status, ["success", "failed"])
      ) {
        invalidEvent();
      }
      return value as unknown as DoneEvent;
    default:
      return invalidEvent();
  }
}

export function createInitialAgentStreamState(): AgentStreamState {
  return {
    answer: "",
    steps: [],
    sources: [],
    terminationReason: "",
    selectedTool: "",
    toolStatus: "",
    error: "",
    completed: false,
  };
}

export function reduceAgentStreamState(
  state: AgentStreamState,
  event: AgentStreamEvent,
): AgentStreamState {
  switch (event.type) {
    case "answer_delta":
      return {...state, answer: state.answer + event.data.text};
    case "step":
      return {...state, steps: [...state.steps, event.data]};
    case "sources":
      return {...state, sources: event.data.sources};
    case "error":
      return {...state, error: event.data.message};
    case "done":
      return {
        ...state,
        terminationReason: event.data.termination_reason,
        selectedTool: event.data.selected_tool,
        toolStatus: event.data.tool_status,
        completed: true,
      };
  }
}

// 首事件 P95 约 23.8s（agent/experiments/reports/streaming/baseline_2026-08-03.md），
// 取约 2.5 倍作为「多久没有任何数据就判定卡死」的上限。
export const AGENT_STREAM_STALL_TIMEOUT_MS = 60_000;

export async function readAgentErrorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && isNonEmptyString(body.error)) return body.error;
  } catch {
    // 上游可能返回非 JSON 响应体，此时回退到状态码文案。
  }
  return `后端返回 ${response.status}`;
}

export interface AgentStreamWatchdog {
  reset: () => void;
  clear: () => void;
}

export function createStallWatchdog(
  onStall: () => void,
  timeoutMs: number = AGENT_STREAM_STALL_TIMEOUT_MS,
): AgentStreamWatchdog {
  let timer: ReturnType<typeof setTimeout> | null = null;

  const clear = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
  };

  return {
    reset() {
      clear();
      timer = setTimeout(onStall, timeoutMs);
    },
    clear,
  };
}

export async function readAgentStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: AgentStreamEvent) => void,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let sawDone = false;

  const consume = (line: string) => {
    if (!line.trim()) return;
    if (sawDone) {
      throw new AgentStreamProtocolError(
        "event_after_done",
        "完成事件之后仍收到数据",
      );
    }
    const event = parseAgentEvent(line);
    onEvent(event);
    if (event.type === "done") sawDone = true;
  };

  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});

      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        consume(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }

    buffer += decoder.decode();
    if (buffer.trim()) consume(buffer);
    if (!sawDone) {
      throw new AgentStreamProtocolError(
        "stream_ended_early",
        "Agent 流在完成前意外中断",
      );
    }
  } finally {
    reader.releaseLock();
  }
}
