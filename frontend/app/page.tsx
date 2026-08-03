"use client";

import {useEffect, useRef, useState} from "react";

import {buildAgentStepViewModel} from "./agent-view-model";
import {
  AGENT_STREAM_STALL_TIMEOUT_MS,
  AgentStreamProtocolError,
  createInitialAgentStreamState,
  createStallWatchdog,
  readAgentErrorMessage,
  readAgentStream,
  reduceAgentStreamState,
} from "./agent-stream";

const EXAMPLE_QUESTIONS = [
  "Qdrant 在向量检索中有什么作用？",
  "LangChain 和 LlamaIndex 有什么区别？",
];

function sourceText(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value : "未提供";
}

function optionalSourceText(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value : null;
}

export default function Home() {
  const [question, setQuestion] = useState("");
  const [streamState, setStreamState] = useState(createInitialAgentStreamState);
  const [hasStarted, setHasStarted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || loading) return;

    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setLoading(true);
    setError("");
    setStreamState(createInitialAgentStreamState());
    setHasStarted(true);

    let stalled = false;
    const watchdog = createStallWatchdog(() => {
      stalled = true;
      controller.abort();
    });
    watchdog.reset();

    try {
      const res = await fetch("/api/agent/stream", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question}),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(await readAgentErrorMessage(res));
      if (!res.body) throw new Error("浏览器未收到响应流");

      await readAgentStream(res.body, (event) => {
        watchdog.reset();
        setStreamState((current) => reduceAgentStreamState(current, event));
        if (event.type === "error") setError(event.data.message);
      });
    } catch (err) {
      if (stalled) {
        setError(
          `Agent 超过 ${AGENT_STREAM_STALL_TIMEOUT_MS / 1000} 秒没有响应，已中断本次请求`,
        );
      } else if (err instanceof DOMException && err.name === "AbortError") {
        return;
      } else if (
        err instanceof AgentStreamProtocolError &&
        err.code === "stream_ended_early"
      ) {
        setError("响应流意外中断，已保留当前收到的内容");
      } else {
        setError(err instanceof Error ? err.message : "请求失败");
      }
    } finally {
      watchdog.clear();
      setLoading(false);
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }

  return (
    <main className="min-h-screen px-5 py-10 sm:px-8 sm:py-16">
      <div className="mx-auto max-w-5xl">
        <header className="max-w-3xl">
          <p className="inline-flex rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-semibold tracking-wide text-blue-700 uppercase">
            Backend-first AI application
          </p>
          <h1 className="mt-5 text-4xl font-semibold tracking-tight text-slate-950 sm:text-5xl">
            RAG 知识库问答平台
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
            基于文档解析、向量检索与大模型生成的问答系统，返回可追溯来源与完整执行轨迹。
          </p>
          <div className="mt-5 flex flex-wrap gap-2 text-sm text-slate-600">
            {['RAG', 'Agent Loop', 'Sources', 'Tool Steps'].map((item) => (
              <span key={item} className="rounded-full border border-slate-200 bg-white px-3 py-1 shadow-sm">
                {item}
              </span>
            ))}
          </div>
        </header>

        <section className="mt-10 rounded-3xl border border-slate-200 bg-white p-5 shadow-xl shadow-slate-200/60 sm:p-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-950">向知识库提问</h2>
              <p className="mt-1 text-sm text-slate-500">Agent 会自主选择工具、执行多轮检索，并基于来源生成答案。</p>
            </div>
            <span className="hidden items-center gap-2 rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700 sm:flex">
              <span className="h-2 w-2 rounded-full bg-emerald-500" />
              Local demo
            </span>
          </div>

          <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-3 sm:flex-row">
            <label htmlFor="question" className="sr-only">问题</label>
            <input
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="例如：LangChain 和 LlamaIndex 有什么区别？"
              className="min-h-12 flex-1 rounded-xl border border-slate-300 bg-slate-50 px-4 py-3 text-slate-950 outline-none transition placeholder:text-slate-400 focus:border-blue-500 focus:bg-white focus:ring-4 focus:ring-blue-100"
            />
            <button
              type="submit"
              disabled={loading || !question.trim()}
              className="min-h-12 rounded-xl bg-slate-950 px-6 py-3 font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {loading ? "检索生成中…" : "开始提问"}
            </button>
          </form>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-slate-400">示例问题</span>
            {EXAMPLE_QUESTIONS.map((item) => (
              <button
                key={item}
                type="button"
                disabled={loading}
                onClick={() => setQuestion(item)}
                className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 transition hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700 disabled:opacity-50"
              >
                {item}
              </button>
            ))}
          </div>

          {loading && (
            <div className="mt-6 flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">
              <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-blue-500" />
              Agent 正在规划并执行工具调用，复杂问题可能需要多轮处理。
            </div>
          )}

          {error && (
            <div className="mt-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              <p className="font-semibold">请求失败</p>
              <p className="mt-1">{error}</p>
            </div>
          )}
        </section>

        {hasStarted && (
          <section className="mt-8 space-y-6" aria-live="polite">
            <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-lg shadow-slate-200/50 sm:p-8">
              <div className="flex flex-col gap-4 border-b border-slate-100 pb-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">Agent result</p>
                  <h2 className="mt-1 text-2xl font-semibold text-slate-950">回答结果</h2>
                </div>
                <div className="flex flex-wrap gap-2 text-xs font-medium">
                  <span className="rounded-full bg-blue-50 px-3 py-1.5 text-blue-700">
                    来源 · {streamState.sources.length}
                  </span>
                  <span className="rounded-full bg-emerald-50 px-3 py-1.5 text-emerald-700">
                    编排轮次 · {streamState.steps.length}
                  </span>
                </div>
              </div>

              <div className="pt-6">
                <p className="whitespace-pre-wrap text-[15px] leading-7 text-slate-700">
                  {streamState.answer || (loading ? "正在生成回答…" : "暂无回答")}
                </p>
                <div className="mt-5 flex flex-wrap gap-2 text-xs">
                  {streamState.selectedTool && (
                    <span className="rounded-full bg-slate-100 px-3 py-1.5 text-slate-600">
                      最终工具 · {streamState.selectedTool}
                    </span>
                  )}
                  {streamState.terminationReason && (
                    <span className="rounded-full bg-violet-50 px-3 py-1.5 text-violet-700">
                      终止原因 · {streamState.terminationReason.replaceAll("_", " ")}
                    </span>
                  )}
                </div>
              </div>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              {!!streamState.sources.length && (
                <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-lg shadow-slate-200/50 sm:p-8">
                  <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">Evidence</p>
                  <h2 className="mt-1 text-xl font-semibold text-slate-950">检索来源 · {streamState.sources.length}</h2>
                  <ul className="mt-5 space-y-3">
                    {streamState.sources.map((source, index) => (
                      <li key={`${sourceText(source, "source")}-${index}`} className="rounded-2xl border border-slate-200 p-4">
                        <div className="flex items-center justify-between gap-3">
                          <p className="min-w-0 truncate font-medium text-slate-900">
                            {sourceText(source, "source")}
                          </p>
                          <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-500">
                            来源 {index + 1}
                          </span>
                        </div>
                        {optionalSourceText(source, "section_path") && (
                          <p className="mt-2 text-xs text-slate-500">
                            {optionalSourceText(source, "section_path")}
                          </p>
                        )}
                        <p className="mt-3 line-clamp-4 text-sm leading-6 text-slate-600">
                          {sourceText(source, "snippet")}
                        </p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-lg shadow-slate-200/50 sm:p-8">
                <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">Agent orchestration</p>
                <h2 className="mt-1 text-xl font-semibold text-slate-950">多步工具编排</h2>
                {streamState.steps.length ? (
                  <ol className="mt-5 space-y-3">
                    {streamState.steps.map((step, index) => {
                      const stepView = buildAgentStepViewModel(step);

                      return (
                        <li key={`${step.round}-${step.status}-${index}`} className="rounded-2xl border border-slate-200 p-4">
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div>
                              <p className="text-xs font-medium text-blue-600">{stepView.roundLabel}</p>
                              <p className="mt-1 font-semibold text-slate-900">{stepView.toolLabel}</p>
                            </div>
                            <div className="flex flex-wrap gap-2 text-xs">
                              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-slate-600">
                                {stepView.statusLabel}
                              </span>
                              {stepView.toolStatusLabel && (
                                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700">
                                  {stepView.toolStatusLabel}
                                </span>
                              )}
                            </div>
                          </div>
                          {stepView.argsLabel && (
                            <p className="mt-3 break-all rounded-lg bg-slate-50 px-3 py-2 font-mono text-xs leading-5 text-slate-600">
                              {stepView.argsLabel}
                            </p>
                          )}
                        </li>
                      );
                    })}
                  </ol>
                ) : (
                  <p className="mt-5 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-600">
                    {loading
                      ? "Agent 正在决定是否调用工具。"
                      : `本次请求未产生多步调用，最终工具为 ${streamState.selectedTool || "未选择"}。`}
                  </p>
                )}
              </div>
            </div>
          </section>
        )}

        <footer className="mt-10 text-center text-xs text-slate-400">
          Local engineering demo · Next.js BFF · FastAPI · LangChain · Qdrant
        </footer>
      </div>
    </main>
  );
}
