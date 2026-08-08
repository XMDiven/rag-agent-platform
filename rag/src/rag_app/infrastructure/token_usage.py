"""按请求累计 LLM token 用量与成本。

计数器由中间件创建并放进 contextvar，回调只**修改**它而不重新 set：
同步路由跑在线程池里，子线程的 set() 对中间件不可见，只有共享同一个
可变对象才能把用量带回去。
"""

import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from rag_app.config import config

logger = logging.getLogger(__name__)


@dataclass
class RequestUsage:
    llm_calls: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
    ) -> None:
        with self._lock:
            self.llm_calls += 1
            self.input_tokens += input_tokens
            self.cached_input_tokens += cached_input_tokens
            self.output_tokens += output_tokens

    @property
    def billable_input_tokens(self) -> int:
        return max(self.input_tokens - self.cached_input_tokens, 0)

    def cost(self) -> float:
        """价格默认为 0：token 数永远统计，金额只在配置了单价后才有意义。"""
        return round(
            (
                self.billable_input_tokens
                * config.LLM_INPUT_PRICE_PER_MILLION
                + self.cached_input_tokens
                * config.LLM_CACHED_INPUT_PRICE_PER_MILLION
                + self.output_tokens * config.LLM_OUTPUT_PRICE_PER_MILLION
            )
            / 1_000_000,
            6,
        )


_request_usage: ContextVar[RequestUsage | None] = ContextVar(
    "request_usage",
    default=None,
)


def start_request_usage() -> RequestUsage:
    usage = RequestUsage()
    _request_usage.set(usage)
    return usage


def get_request_usage() -> RequestUsage | None:
    return _request_usage.get()


def extract_usage(message: Any) -> tuple[int, int, int] | None:
    metadata = getattr(message, "usage_metadata", None)
    if not isinstance(metadata, dict):
        return None

    details = metadata.get("input_token_details") or {}
    return (
        int(metadata.get("input_tokens", 0)),
        int(details.get("cache_read", 0)),
        int(metadata.get("output_tokens", 0)),
    )


class TokenUsageCallback(BaseCallbackHandler):
    """挂在客户端上，覆盖所有调用点。

    实测确认：即使调用链末端是 StrOutputParser（会丢掉 AIMessage），
    回调依然能拿到 usage_metadata，所以不需要改造任何现有调用代码。
    """

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = get_request_usage()
        if usage is None:
            return

        for generations in getattr(response, "generations", []):
            for generation in generations:
                extracted = extract_usage(getattr(generation, "message", None))
                if extracted is not None:
                    usage.add(*extracted)


def log_request_usage(usage: RequestUsage) -> None:
    if usage.llm_calls == 0:
        return

    logger.info(
        "usage.request llm_calls=%s input=%s cached_input=%s output=%s "
        "total=%s cost=%.6f",
        usage.llm_calls,
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.input_tokens + usage.output_tokens,
        usage.cost(),
    )
