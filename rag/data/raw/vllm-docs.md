# vLLM Docs

Source: https://docs.vllm.ai/

[](https://github.com/vllm-project/vllm/edit/main/docs/README.md "Edit this page")

## Welcome to vLLM[¶](https://docs.vllm.ai/#welcome-to-vllm "Permanent link")

[![vLLM Light](https://docs.vllm.ai/assets/logos/vllm-logo-text-light.png)](https://docs.vllm.ai/assets/logos/vllm-logo-text-light.png) [![vLLM Dark](https://docs.vllm.ai/assets/logos/vllm-logo-text-dark.png)](https://docs.vllm.ai/assets/logos/vllm-logo-text-dark.png)

**Easy, fast, and cheap LLM serving for everyone**

[Star](https://github.com/vllm-project/vllm) [Watch](https://github.com/vllm-project/vllm/subscription) [Fork](https://github.com/vllm-project/vllm/fork)

vLLM is a fast and easy-to-use library for LLM inference and serving.

Originally developed in the [Sky Computing Lab](https://sky.cs.berkeley.edu/) at UC Berkeley, vLLM has evolved into a community-driven project with contributions from both academia and industry.

Where to get started with vLLM depends on the type of user. If you are looking to:

*   Run open-source models on vLLM, we recommend starting with the [Quickstart Guide](https://docs.vllm.ai/getting_started/quickstart/)
*   Build applications with vLLM, we recommend starting with the [User Guide](https://docs.vllm.ai/usage/)
*   Build vLLM, we recommend starting with [Developer Guide](https://docs.vllm.ai/contributing/)

For information about the development of vLLM, see:

*   [Roadmap](https://roadmap.vllm.ai/)
*   [Releases](https://github.com/vllm-project/vllm/releases)

vLLM is fast with:

*   State-of-the-art serving throughput
*   Efficient management of attention key and value memory with [**PagedAttention**](https://blog.vllm.ai/2023/06/20/vllm.html)
*   Continuous batching of incoming requests
*   Fast model execution with CUDA/HIP graph
*   Quantization: [GPTQ](https://arxiv.org/abs/2210.17323), [AWQ](https://arxiv.org/abs/2306.00978), INT4, INT8, and FP8
*   Optimized CUDA kernels, including integration with FlashAttention and FlashInfer.
*   Speculative decoding
*   Chunked prefill

vLLM is flexible and easy to use with:

*   Seamless integration with popular HuggingFace models
*   High-throughput serving with various decoding algorithms, including _parallel sampling_, _beam search_, and more
*   Tensor, pipeline, data and expert parallelism support for distributed inference
*   Streaming outputs
*   OpenAI-compatible API server
*   Support for NVIDIA GPUs, AMD CPUs and GPUs, Intel CPUs and GPUs, PowerPC CPUs, Arm CPUs, and TPU. Additionally, support for diverse hardware plugins such as Intel Gaudi, IBM Spyre and Huawei Ascend.
*   Prefix caching support
*   Multi-LoRA support

For more information, check out the following:

*   [vLLM announcing blog post](https://blog.vllm.ai/2023/06/20/vllm.html) (intro to PagedAttention)
*   [vLLM paper](https://arxiv.org/abs/2309.06180) (SOSP 2023)
*   [How continuous batching enables 23x throughput in LLM inference while reducing p50 latency](https://www.anyscale.com/blog/continuous-batching-llm-inference) by Cade Daniel et al.
*   [vLLM Meetups](https://docs.vllm.ai/community/meetups/)
