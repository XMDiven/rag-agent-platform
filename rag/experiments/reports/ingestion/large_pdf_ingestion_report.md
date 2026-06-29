# 100+ 页 PDF 摄入验证报告

## 测试目标

验证当前 RAG 离线索引流程已经能够处理 100+ 页 PDF，并完成：

- PDF 页面解析
- 文档切分
- chunk 元数据构建
- Qdrant 向量入库
- 后续检索和回答评估命中该 PDF 来源

本报告复用最近一次完整 `build_index` 运行记录，没有重新触发全量索引构建。原因是当前语料规模较大，全量重建索引耗时较长；为了避免重复消耗，本报告使用已有入库日志，并通过当前 Qdrant collection 和 evaluation report 做补充验证。

## 测试文件

- 文件路径：`data/raw/gpt4_technical_report.pdf`
- 文件类型：PDF
- 文件大小：约 5.0 MB
- 页数：100 页
- 选择原因：该 PDF 达到 100 页级别，适合验证大 PDF 的解析、切分、入库和检索链路。

页数验证命令：

```bash
conda run -n AI_DEV python -c "from pypdf import PdfReader; print(len(PdfReader('rag/data/raw/gpt4_technical_report.pdf').pages))"
```

输出：

```text
100
```

## 入库记录

最近一次完整 `build_index` 运行中，该 PDF 的索引输出为：

```text
indexed /Users/mdiven/Code/Projects/rag-agent-platform/rag/data/raw/gpt4_technical_report.pdf documents=100 chunks=452 stored=452
```

字段含义：

- `documents=100`：PDF 被解析为 100 个页面级 document。
- `chunks=452`：页面内容被切分为 452 个 chunk。
- `stored=452`：452 个 chunk 成功写入 Qdrant。

## 当前 Qdrant 状态验证

为避免只依赖历史终端输出，本报告额外检查了当前 Qdrant collection 中该 PDF 来源对应的 point 数量。

验证命令：

```bash
conda run -n AI_DEV python -c "from qdrant_client import QdrantClient, models; from rag_app.config import config; import os; client=QdrantClient(url=os.environ['QDRANT_URL']); source='data/raw/gpt4_technical_report.pdf'; result=client.count(collection_name=config.COLLECTION_NAME, count_filter=models.Filter(must=[models.FieldCondition(key='metadata.source', match=models.MatchValue(value=source))]), exact=True); print(result.count)"
```

输出：

```text
452
```

这说明当前 Qdrant collection 中仍然存在 452 个来自 `data/raw/gpt4_technical_report.pdf` 的 chunk，与历史入库日志中的 `stored=452` 一致。

## 检索验证

最新代表性 evaluation report：

```text
experiments/runs/evaluation/20260524-213237.json
```

其中 retrieval case `gpt4_report_scope` 通过，并命中了目标 PDF：

```json
{
  "id": "gpt4_report_scope",
  "question": "What does the GPT-4 technical report say about its scope and limitations?",
  "passed": true,
  "expected_source_contains": [
    "gpt4_technical_report.pdf"
  ],
  "retrieved_sources": [
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf"
  ]
}
```

这说明该 100 页 PDF 不仅完成了入库，也能在真实检索评估中被命中。

## 回答验证

同一 evaluation report 中，answer case `gpt4_report_scope` 也通过，并返回了该 PDF 作为来源：

```json
{
  "id": "gpt4_report_scope",
  "question": "What does the GPT-4 technical report say about its scope and limitations?",
  "passed": true,
  "failures": [],
  "sources": [
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf",
    "data/raw/gpt4_technical_report.pdf"
  ]
}
```

这说明该 PDF 可以进入完整问答链路，并作为 grounded answer 的来源返回。

## 结论

当前 RAG 离线索引流程已经验证可以处理 100 页 PDF，并完成解析、切分、向量化入库、检索命中和回答来源返回。

该报告可以支撑以下简历表述：

```text
验证 100+ 页 PDF 文档的解析、切分、向量化入库和检索命中流程，并通过实验报告记录 documents/chunks/stored 等处理结果。
```

## 当前边界

- 本报告复用已有索引构建日志，没有重新运行全量 `build_index`。
- 当前验证的是离线索引流程，不是上传后自动入库。
- 当前没有验证多个大 PDF 的并发上传或并发入库。
- 当前没有记录该 PDF 的完整处理耗时，因此不能支撑性能 SLA。
- 当前不能据此声称“支持任意大小 PDF”或“生产级大文件并发处理”。
