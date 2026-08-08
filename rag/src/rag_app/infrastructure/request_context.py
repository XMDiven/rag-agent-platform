"""跨调用链传递 request-id，让一次请求产生的所有日志能被串起来。

放在 `rag_app` 而不是新建共享包，是因为 `agent_app` 已经依赖 `rag_app`
（`retrieval_tool` 直接调用 `ask_question`），两个服务共用同一份实现即可。
同进程内调用时 contextvar 会自然携带，不需要显式传参。
"""

import logging
import re
import uuid
from contextvars import ContextVar, Token

from rag_app.infrastructure.token_usage import (
    log_request_usage,
    start_request_usage,
)

REQUEST_ID_HEADER = "x-request-id"
MISSING_REQUEST_ID = "-"
MAX_REQUEST_ID_LENGTH = 64

# 客户端传入的 id 会被写进日志，必须限制字符集，否则换行符可以伪造日志行。
SAFE_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

_request_id: ContextVar[str] = ContextVar(
    "request_id",
    default=MISSING_REQUEST_ID,
)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def sanitize_request_id(value: str | None) -> str | None:
    if not value:
        return None

    candidate = value.strip()[:MAX_REQUEST_ID_LENGTH]
    if not candidate or not SAFE_REQUEST_ID_PATTERN.match(candidate):
        return None

    return candidate


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> Token[str]:
    return _request_id.set(value)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """把当前 request-id 贴到每条日志上，调用方无需改动日志语句。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class RequestIdMiddleware:
    """纯 ASGI 中间件。

    不用 `BaseHTTPMiddleware`：它会包一层响应流，对本项目核心的 NDJSON
    流式输出有干扰风险。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for key, value in scope.get("headers", []):
            if key == REQUEST_ID_HEADER.encode():
                incoming = value.decode("latin-1")
                break

        request_id = sanitize_request_id(incoming) or new_request_id()
        token = set_request_id(request_id)
        usage = start_request_usage()

        async def send_with_request_id(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append(
                    (REQUEST_ID_HEADER.encode(), request_id.encode())
                )
                message = {**message, "headers": headers}
            await send(message)

        try:
            # StreamingResponse 会等响应体流完才返回，所以这里的统计是完整的。
            await self.app(scope, receive, send_with_request_id)
        finally:
            log_request_usage(usage)
            reset_request_id(token)


LOG_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - "
    "[request_id=%(request_id)s] %(message)s"
)


def configure_request_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format=LOG_FORMAT)

    request_id_filter = RequestIdFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(request_id_filter)
