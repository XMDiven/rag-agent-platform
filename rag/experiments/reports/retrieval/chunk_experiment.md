# 检索切块实验

| 运行 | CHUNK_SIZE | CHUNK_OVERLAP | chunk 数 | 入库数 | retrieval_eval | 备注 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| baseline | 200 | 30 | 12268 | 12268 | 8/8 passed | 扩展后的 golden cases 都命中了期望 sources。 |
| variant-1 | 400 | 50 | 5371 | 5369 | 8/8 passed | chunk 数明显下降，同时所有 golden cases 仍然命中期望 sources。 |
| variant-2 | 800 | 100 | 2779 | 2777 | 8/8 passed | 测试过的最大 chunk 仍然保持 source hit，同时进一步降低索引规模。 |

## Retrieval Top-K 实验

| 运行 | RETRIEVAL_TOP_K | retrieval_eval | answer_eval | 备注 |
| --- | ---: | --- | --- | --- |
| baseline | 2 | 10/11 passed | 未运行 | 多 source 对比 case 漏掉了 LangChain source。 |
| variant-1 | 5 | 11/11 passed | 11/11 passed | 更高召回覆盖了 LangChain 和 LlamaIndex，同时回答质量可接受。 |
| variant-2 | 7 | 11/11 passed | 11/11 passed | 在扩展语料后恢复了多 source 对比 case，能同时检索到 LangChain 和 LlamaIndex。 |

结论：`RETRIEVAL_TOP_K = 7` 是当前已验证的基线。它能在扩展语料上恢复多 source recall，并保持当前 answer evaluation 通过。
