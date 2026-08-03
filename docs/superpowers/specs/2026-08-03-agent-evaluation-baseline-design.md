# Agent 离线评测基线设计

## 目标

为现有多步 Agent 增加一套可重复运行的离线评测基线，用确定性指标回答三个问题：Agent 是否选择了合理工具、是否正常结束、运行轨迹和延迟是否出现回归。

本次只建立最小评测闭环，不改变 Agent 运行逻辑，不引入新依赖，也不加入 LLM-as-Judge、在线观测平台、长期记忆或新工具。

## 方案选择

考虑过三种方案：

1. 直接复用现有单元测试。成本最低，但 FakeModel 只能证明编排代码按预设响应运行，不能评估真实模型决策。
2. 建立仓库内 JSON golden set 和 Python runner。能够评估真实 `run_agent()` 输出，结果可版本化、可复现，且不增加依赖。本次采用该方案。
3. 接入 LangSmith 或其他 Agent 评测平台。可视化和数据管理更强，但会引入外部服务、配置和成本，超出当前学习项目的最小需求。

## 范围

第一版数据集包含 10 至 15 个代表性问题，覆盖：

- 直接回答或无需检索的问题；
- 单次知识库检索；
- 对比问题的拆解、多次检索和综合；
- 摘要请求；
- 空输入的规则式降级；
- 正常 `final_answer` 和触发 `max_steps` 的可观测性。

每个 case 只保存稳定、可机械判断的字段：

- `id`：稳定标识；
- `question`：输入问题；
- `allowed_tools`：允许出现的工具集合；
- `required_tools`：必须至少出现一次的工具集合；
- `allowed_termination_reasons`：允许的结束原因；
- `requires_sources`：是否必须返回来源；
- `max_steps`：该 case 允许的最大轨迹步数。

不对自然语言答案做字符串精确匹配，避免将模型措辞波动误判为回归。

## 组件与职责

### 数据集

`agent/experiments/datasets/agent_eval_cases.json` 由人工维护，是评测输入和预期契约。字段在 runner 加载时校验；无效 case 应立即报错并指出 case id。

### 评测 runner

`agent/src/agent_app/scripts/evaluate_agent.py` 负责：

1. 加载数据集；
2. 对每个问题调用现有 `run_agent()`；
3. 从 `steps` 提取实际工具序列；
4. 判断工具约束、结束原因、来源要求和步数限制；
5. 记录单 case 延迟与失败原因；
6. 汇总正常结束率、case 通过率、工具约束通过率、来源通过率、平均延迟和 P95 延迟；
7. 将完整 JSON 结果写入 `agent/experiments/runs/evaluation/<timestamp>.json`。

纯计算逻辑与 I/O 分离：case 判断和汇总函数接收普通 Python 数据，便于不调用真实模型的单元测试；CLI 层才负责读取文件、调用 Agent 和写结果。

### 文档

`agent/experiments/README.md` 说明目录职责、运行命令和指标含义。根 README 和 `agent/README.md` 同步修正已经过时的 Agent 能力边界，并链接评测入口。

## 数据流

```text
golden set -> evaluate_agent runner -> run_agent(question)
           -> steps/sources/termination_reason
           -> deterministic checks
           -> per-case results + summary
           -> timestamped JSON run
```

## 错误处理

- 数据集文件不存在、JSON 非法或字段类型错误：立即失败，不生成误导性报告。
- 单个 Agent case 抛出异常：记录该 case 为失败并继续剩余 case，只持久化异常类型；异常消息可能包含密钥、认证 URL 或请求内容，不写入报告。
- 输出目录不存在：runner 创建目标目录。
- 不捕获 `KeyboardInterrupt` 等进程级中断。
- 报告不得写入密钥、完整模型消息历史或文档正文，只保存问题、轨迹摘要、来源元数据、结果和耗时。

CLI 在所有 case 通过时返回退出码 0；存在失败 case 时返回退出码 1，便于以后选择性接入 CI。真实模型评测第一版不直接加入默认 GitHub Actions，避免外部模型和 Qdrant 依赖导致 CI 不稳定。

## 测试策略

严格按 TDD 实现：

- 数据集校验测试；
- 工具序列、结束原因、来源和步数判断测试；
- P95 与汇总指标测试；
- 单 case 异常后继续运行测试；
- JSON 报告写入测试；
- CLI 退出码测试。

测试注入假的 `run_agent` 函数，不访问 LLM 或 Qdrant。完成后运行完整 `uv run pytest -q`，并运行前端 lint 与 production build，证明没有破坏现有工程。

## 成功标准

- 提供 10 至 15 个有明确预期的 Agent case；
- runner 可通过 `uv run python -m agent_app.scripts.evaluate_agent` 执行；
- 每次执行生成一个带时间戳、包含逐 case 结果和汇总指标的 JSON 文件；
- 失败原因足够定位到具体 case 和具体约束；
- 新增逻辑有确定性单元测试，完整 Python 测试套件通过；
- 不新增依赖，不改变 Agent API 或运行行为。

## 后续但不在本次范围

- 用 LLM-as-Judge 评估答案质量；
- 记录 token 与模型调用成本；
- 将真实模型评测接入定时 CI；
- 并行工具调用、联网搜索、会话记忆和多 Agent。
