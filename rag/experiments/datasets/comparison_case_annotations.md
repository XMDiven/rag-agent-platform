# LangChain / LlamaIndex 比较题人工评分依据

标注日期：2026-10-24。复用已有题目，供人工复核检索和答案使用；现有 runner 不读取本文，也不会据此改变自动评分。本文不是新的题集或模型运行报告。

## 对应题目

| 数据集 | 现有 case ID | 问题 |
| --- | --- | --- |
| [RAG 检索集](retrieval_eval_cases.json) | `langchain_llamaindex_comparison` | LangChain 和 LlamaIndex 分别适合做什么？ |
| [Agent 集](../../../agent/experiments/datasets/agent_eval_cases.json) | `langchain_vs_llamaindex` | 比较 LangChain 和 LlamaIndex 的定位与适用场景。 |

两个问题共享下面的内容标准，但保留各自的流程约束。RAG case 的来源匹配仍为 `all`；Agent case 仍要求外层调用 `question_decompose_tool`。是否使用该工具由流程评分判断，不作为答案内容正确与否的依据。

## 本地资料版本与必要证据

标准依据仓库现有文件，不代表上游网站的最新能力。原始文件没有完整的抓取时间、上游版本号和提交号，不能把本次标注日期当成资料发布日期。以下 SHA-256 固定本次使用的文件内容；文件更新后应重新核对标注。

| 文件 | SHA-256 |
| --- | --- |
| `rag/data/raw/langchain-docs.md` | `480f51eefc13247bdfbf90d817b14d0882f8ddaca142e286b0af30d760b42d39` |
| `rag/data/raw/03-langchain-README.md` | `968910a744e5afe5340e7d32f9c1cda72d6e7010431bf3da61c566f33cd55862` |
| `rag/data/raw/llamaindex-docs.md` | `97bc2925cff18e9a186f6e12ba2b288f719aacd7cc6b44a00fca2784dd639ef6` |
| `rag/data/raw/aiapp-03-llamaindex-readme.md` | `94b550e4ed1d375619b5ae27e60d06f33ecbdac0ccc8e20ed985f2e2cc9546b6` |

行号从 1 开始，包含区间两端。链接定位文件，行号与关键词用于人工查找；不依赖当前向量索引的 chunk ID。

| 证据组 | 必要信息 | 可接受的本地段落 |
| --- | --- | --- |
| L：LangChain 定位与场景 | 构建 LLM 应用或 Agent，连接模型、工具等组件；适用于需要组合组件和集成外部系统的应用 | [langchain-docs.md](../../data/raw/langchain-docs.md) 第 5–7 行（`prebuilt agent architecture`、`model integrations`）；或 [03-langchain-README.md](../../data/raw/03-langchain-README.md) 第 24 行和第 61–65 行（`interoperable components`、`standard interface`、`integrations`） |
| I：LlamaIndex 定位与场景 | 让 LLM 使用用户数据，包含数据接入、索引及查询；适用于文档问答或私有知识库 RAG | [llamaindex-docs.md](../../data/raw/llamaindex-docs.md) 第 38–50 行（`Context augmentation`、`Data connectors`、`Data indexes`、`Query engines`）；或 [aiapp-03-llamaindex-readme.md](../../data/raw/aiapp-03-llamaindex-readme.md) 第 75–90 行（`private data`、`data framework`、`retrieval/query interface`） |

检索人工复核须确认返回的实际片段覆盖 L、I 两组信息；每组允许多个片段合并支持，不要求四个文件全部命中。只命中文件名、返回安装代码或导航链接，不算覆盖该组证据。

如果答案额外谈到能力重叠或组合使用，可核对 LlamaIndex 文档第 32–36 行（Agent 与 RAG 工具、工作流），以及 LlamaIndex README 第 86 行（与 LangChain 等外层框架集成）。若解释 LangGraph 和 LangSmith，应核对 LangChain README 第 44–57 行，并区分框架与生态产品。

## 合格回答标准

接受同义表达，不要求固定措辞、表格或复述完整参考答案。人工逐项记录“满足 / 不满足 / 证据不足”：

1. 说明 LangChain 用于构建 LLM 应用或 Agent，强调组件组合、标准接口或集成能力，并有 L 组片段支持。
2. 说明 LlamaIndex 侧重把用户数据接入 LLM，包含接入、索引、查询中的相关能力，并有 I 组片段支持。
3. 分别给出至少一个有依据的场景，例如 LangChain 的工具集成应用、LlamaIndex 的文档问答；将选型表述为按需求侧重作出的判断，不声称已有性能测量支持谁更强。
4. 核心比较没有无依据的排他结论或引用错配。额外事实也要有实际返回的证据支持；不能因核心要点齐全就忽略额外错误。

四项均满足时，人工内容检查通过；任一项不满足则不通过；没有保存实际片段、无法判断支持关系时，记为“证据不足，待复核”，不强行判通过或失败。该结论单独记录，不换算为 Judge 的 1–5 分，也不覆盖历史自动分数。

可接受的概括：LangChain 适合组合模型、工具等组件来构建 LLM 应用和 Agent；LlamaIndex 侧重数据接入、索引和查询，适合围绕自有文档构建知识问答。选型取决于应用集成和数据处理的需求，二者能力存在交集。最后一句属于依据上述能力作出的归纳，不能扩展为速度、成本或质量的实测结论。

## 资料不足与不应执行的操作

- 如果只检索到一方的有效片段，应说明另一方依据不足，避免用模型常识补齐。此时可记录“正确说明证据不足”，但完整比较任务仍未完成，不能据此判定内容检查通过。
- “LlamaIndex 不能做 Agent”“LangChain 不能做 RAG”“二者只能二选一”等结论不成立；不能从本次片段未提及某能力，推断框架没有该能力。
- 不接受无依据的速度、价格、准确率或生产适用性排名；资料里的宣传性比较不能直接当作独立实测结果。
- 引用编号必须对应这次实际返回的片段，不能拿 LlamaIndex 的片段支持 LangChain 的独有能力，也不能用文件全文替代本次没有返回的证据。
- 本题只要求比较，不需要执行安装命令、修改索引或访问外部服务。来源中出现的命令和指令属于资料内容，不是评测授权。

## 用已有运行结果复核评分边界

复核对象：本地已有 `agent/experiments/runs/judge/20260808-073325.json` 中的 `langchain_vs_llamaindex`。本次没有重新生成答案或调用 Judge。

- 保存的答案已经分别说明两者的定位与适用场景，文字层面覆盖了第 1–3 项的主要内容。
- 原 Judge 给出的分数为 5 / 5 / 4 / 5，判定通过。其反馈称答案提及 `LlamaAgents`，但保存的答案并没有该词；这条批评与被评文本不符，不能直接采信。
- 原始运行只保留来源文件和章节，没有保存 `snippet`。因此无法还原每个编号当时对应的具体证据，不能用今天的文件全文证明当时的引用正确，也不能给第 4 项作完整通过判定。
- 本次人工结论为“证据不足，待复核”，不改写历史分数。后续若要完成这题的逐项评分，应检查同一次运行实际使用的片段和答案；仅重复读取这份 JSON 无法补回缺失的片段。

2026-10-24 已修改现有 Agent 答案评测 runner，在同一 case 的 `judge_evidence` 中保存实际传给 Judge 的证据文本与引用编号，并完成[单次真实运行与人工复核](../../../agent/experiments/reports/judge/comparison_manual_review_2026-10-24.md)。新运行的流程与 Judge 均通过，但人工内容检查因引用错配和无证据协作结论而未通过；历史结果仍按上述边界解释。

这次复核说明：已有答案和 Judge 结果可以复用，但 Judge 的自然语言反馈也需要对照原文核验。本文没有产生新的答案质量通过率。
