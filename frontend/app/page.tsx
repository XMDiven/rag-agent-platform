"use client";

import {useState} from "react";

interface AgentRunResponse{
  answer: string;
  selected_tool: string;
  sources: Array<Record<string , unknown>>
}

const AGENT_API = "http://localhost:8001/agent/run";

export default function Home() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AgentRunResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim() || loading) return;

    setLoading(true);
    setError("");
    setResult(null);

    try {
      const res = await fetch(AGENT_API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!res.ok) throw new Error(`后端返回 ${res.status}`);
      const data: AgentRunResponse = await res.json();
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-6 py-16">
      <h1 className="text-2xl font-semibold">RAG + Agent 问答</h1>

      <form onSubmit={handleSubmit} className="mt-6 flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="例如:LangChain 是什么?"
          className="flex-1 rounded border border-zinc-300 px-3 py-2"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded bg-black px-4 py-2 text-white disabled:opacity-50"
        >
          {loading ? "思考中…" : "提问"}
        </button>
      </form>

      {error && <p className="mt-4 text-red-600">出错了:{error}</p>}

      {result && (
        <section className="mt-8 space-y-4">
          <p className="text-sm text-zinc-500">
            选用工具:<span className="font-mono">{result.selected_tool}</span>
          </p>
          <div>
            <h2 className="font-medium">回答</h2>
            <p className="mt-1 whitespace-pre-wrap">{result.answer}</p>
          </div>
          {result.sources.length > 0 && (
            <div>
              <h2 className="font-medium">来源({result.sources.length})</h2>
              <ul className="mt-1 space-y-2">
                {result.sources.map((src, i) => (
                  <li key={i} className="rounded bg-zinc-100 p-2 text-xs font-mono break-all">
                    {JSON.stringify(src)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </main>
  );
}