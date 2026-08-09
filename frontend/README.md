# Agent 演示前端

这是 `rag-agent-platform` 的本地演示界面，使用 Next.js App Router、React 和 TypeScript。浏览器只访问同源 BFF；BFF 再调用 Agent API，避免把后端地址或服务端配置暴露给客户端。

## 数据流

```text
Browser -> POST /api/agent/stream -> Agent POST /agent/run/stream
        <- versioned NDJSON events <-
```

页面会在请求结束前增量展示：

- Agent 工具调用步骤与状态
- 流式回答内容
- 检索来源
- 最终工具与终止原因
- 上游失败、协议错误和流意外中断

BFF 会生成或透传经过清理的 `X-Request-ID`，便于把浏览器请求与 Agent、RAG 日志关联起来。

## 本地运行

先启动 Agent API，默认地址为 `http://localhost:8002/agent/run/stream`。然后运行：

```bash
cd frontend
npm ci
npm run dev
```

打开 <http://localhost:3000>。

如需覆盖 Agent 流式接口地址，使用仅服务端可见的环境变量，不要添加 `NEXT_PUBLIC_` 前缀：

```bash
AGENT_STREAM_API_URL=http://localhost:8002/agent/run/stream npm run dev
```

完整系统也可以从仓库根目录通过 Docker Compose 启动：

```bash
docker compose up --build -d
```

## 验证

```bash
npm test
npm run lint
npm run build
```

测试覆盖 BFF 输入校验、request-id 透传、首块不缓冲转发、NDJSON 分块解析、UTF-8 边界、状态归并、停滞超时和中断错误映射。生产构建使用 Next.js standalone 输出，对应 [`Dockerfile`](Dockerfile)。

## 配置边界

- `AGENT_STREAM_API_URL` 只在服务端 Route Handler 中读取。
- 当前界面用于本地工程演示，不包含登录、租户隔离或公网部署配置。
- 前端不会直接持有 LLM API key；模型与向量库配置由后端负责。
