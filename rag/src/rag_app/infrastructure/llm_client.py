from langchain_openai import ChatOpenAI

from rag_app.config import config
from rag_app.infrastructure.token_usage import TokenUsageCallback


def get_client() -> ChatOpenAI:
    base_url = config.settings.llm_base_url
    model = config.settings.llm_model_id
    api_key = config.settings.llm_api_key
    thinking_type = config.settings.llm_thinking_type

    if not base_url:
        raise RuntimeError("LLM_BASE_URL is not set")

    if not model:
        raise RuntimeError("LLM_MODEL_ID is not set")

    if not api_key:
        raise RuntimeError("MOONSHOT_API_KEY is not set")

    client_kwargs = {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        # 没有这两项时底层 httpx 的超时是 None（无限等待），一次挂死的上游
        # 调用会永久占住 FastAPI 线程池的一个线程。
        "timeout": config.LLM_TIMEOUT_SECONDS,
        "max_retries": config.LLM_MAX_RETRIES,
        # langchain 只在用官方 OpenAI 地址时才默认开启；本项目走 Moonshot
        # 的 base_url，不显式打开的话流式路径拿不到任何 usage，
        # 而流式恰恰是唯一的用户可见路径。
        "stream_usage": True,
        "callbacks": [TokenUsageCallback()],
    }

    if thinking_type is not None:
        client_kwargs["extra_body"] = {
            "thinking": {
                "type": thinking_type,
            },
        }

    client = ChatOpenAI(**client_kwargs)

    return client
