"use client";

import {useState} from "react";

interface TraceItem {
  step: string;
  status: string;
  detail?: Record<string, unknown>;
}

interface RagAskResponse {
  answer: string;
  trace: TraceItem[];
  sources: Array<Record<string, unknown>>;
}

const RAG_API =
  process.env.NEXT_PUBLIC_RAG_API ?? "http://localhost:8001/ask";

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

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

export default function Home() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<RagAskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || loading) return;

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const res = await fetch(RAG_API, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question}),
      });
      if (!res.ok) throw new Error(`后端返回 ${res.status}`);
      const data: RagAskResponse = await res.json();
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败");
    } finally {
      setLoading(false);
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
            {['RAG', 'Qdrant', 'Sources', 'Trace'].map((item) => (
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
              <p className="mt-1 text-sm text-slate-500">系统会完成问题分析、检索规划、向量召回与答案生成。</p>
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
              系统正在检索知识库并基于召回内容生成答案。
            </div>
          )}

          {error && (
            <div className="mt-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              <p className="font-semibold">请求失败</p>
              <p className="mt-1">{error}</p>
            </div>
          )}
        </section>

        {result && (
          <section className="mt-8 space-y-6" aria-live="polite">
            <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-lg shadow-slate-200/50 sm:p-8">
              <div className="flex flex-col gap-4 border-b border-slate-100 pb-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">RAG result</p>
                  <h2 className="mt-1 text-2xl font-semibold text-slate-950">回答结果</h2>
                </div>
                <div className="flex flex-wrap gap-2 text-xs font-medium">
                  <span className="rounded-full bg-blue-50 px-3 py-1.5 text-blue-700">
                    来源 · {result.sources.length}
                  </span>
                  <span className="rounded-full bg-emerald-50 px-3 py-1.5 text-emerald-700">
                    链路步骤 · {result.trace.length}
                  </span>
                </div>
              </div>

              <div className="pt-6">
                <p className="whitespace-pre-wrap text-[15px] leading-7 text-slate-700">{result.answer}</p>
              </div>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              {!!result.sources.length && (
                <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-lg shadow-slate-200/50 sm:p-8">
                  <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">Evidence</p>
                  <h2 className="mt-1 text-xl font-semibold text-slate-950">检索来源 · {result.sources.length}</h2>
                  <ul className="mt-5 space-y-3">
                    {result.sources.map((source, index) => (
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

              {!!result.trace.length && (
                <div className="rounded-3xl border border-slate-200 bg-white p-5 shadow-lg shadow-slate-200/50 sm:p-8">
                  <p className="text-xs font-semibold tracking-wide text-blue-600 uppercase">Observability</p>
                  <h2 className="mt-1 text-xl font-semibold text-slate-950">执行轨迹</h2>
                  <ul className="mt-5 divide-y divide-slate-100">
                    {result.trace.map((item, index) => (
                      <li key={`${item.step}-${index}`} className="flex items-center justify-between gap-4 py-3 text-sm">
                        <span className="font-medium text-slate-700">{item.step}</span>
                        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-500">
                          {statusLabel(item.status)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </section>
        )}

        <footer className="mt-10 text-center text-xs text-slate-400">
          Local engineering demo · FastAPI · LangChain · Qdrant
        </footer>
      </div>
    </main>
  );
}
