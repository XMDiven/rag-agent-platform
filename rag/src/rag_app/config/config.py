from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
DATA_DIR: Path = PROJECT_ROOT / "data"
RAW_DATA_DIR: Path = DATA_DIR / "raw"

RetrievalSearchType = Literal["similarity", "mmr" , "hybrid"]
RouterBackend = Literal["llm", "finetuned"]
LlmThinkingType = Literal["enabled", "disabled"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    chunk_size: int = 800
    chunk_overlap: int = 100

    retrieval_top_k: int = 7
    retrieval_search_type: RetrievalSearchType = "similarity"
    retrieval_fetch_k: int = 50
    retrieval_lambda_mult: float = 0.3

    retrieval_hybrid_candidate_k: int = 20
    retrieval_hybrid_bm25_weight: float = 0.5
    retrieval_hybrid_dense_weight: float = 0.5

    qdrant_url: str | None = None
    qdrant_collection: str | None = None

    redis_url: str | None = None
    answer_cache_ttl_seconds: int = 3600
    redis_timeout_seconds: float = 0.2

    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_timeout_seconds: float = 30.0

    llm_base_url: str | None = None
    llm_model_id: str | None = None
    # 无超时的上游调用会永久占住 FastAPI 线程池里的一个线程，攒够就整体不可用。
    # 60s 取自实测：/ask P95 约 19.5s，Agent 单轮 LLM 调用约 25-30s。
    llm_timeout_seconds: float = 60.0
    # SDK 默认重试 2 次，最坏耗时是超时时长的 3 倍；这里收紧到 1 次，
    # 且 ask_service 自身还有 MAX_GENERATION_RETRY 一层，两者会叠乘。
    llm_max_retries: int = 1
    # 单价按「每百万 token」配置，默认 0：不写死可能过期的价格，
    # 未配置时只统计 token 数，不产出会被误引用的金额。
    llm_input_price_per_million: float = 0.0
    llm_cached_input_price_per_million: float = 0.0
    llm_output_price_per_million: float = 0.0
    llm_thinking_type: LlmThinkingType | None = None
    moonshot_api_key: str | None = None
    openai_api_key: str | None = None

    qa_prompt_version: str = "qa_prompt_v1"

    # Default keeps the existing behaviour; "finetuned" routes knowledge-tool
    # selection through the agent-toolcall-sft service and degrades back to
    # "llm" on any failure.
    router_backend: RouterBackend = "llm"
    finetuned_router_url: str = "http://127.0.0.1:8000"

    @property
    def llm_api_key(self) -> str | None:
        return self.moonshot_api_key or self.openai_api_key


settings = Settings()

CHUNK_SIZE: int = settings.chunk_size
CHUNK_OVERLAP: int = settings.chunk_overlap
RETRIEVAL_TOP_K: int = settings.retrieval_top_k
RETRIEVAL_SEARCH_TYPE: str = settings.retrieval_search_type
RETRIEVAL_FETCH_K: int = settings.retrieval_fetch_k
RETRIEVAL_LAMBDA_MULT: float = settings.retrieval_lambda_mult
COLLECTION_NAME: str | None = settings.qdrant_collection
REDIS_URL: str | None = settings.redis_url
ANSWER_CACHE_TTL_SECONDS: int = settings.answer_cache_ttl_seconds
REDIS_TIMEOUT_SECONDS: float = settings.redis_timeout_seconds
LLM_TIMEOUT_SECONDS: float = settings.llm_timeout_seconds
LLM_MAX_RETRIES: int = settings.llm_max_retries
LLM_INPUT_PRICE_PER_MILLION: float = settings.llm_input_price_per_million
LLM_CACHED_INPUT_PRICE_PER_MILLION: float = (
    settings.llm_cached_input_price_per_million
)
LLM_OUTPUT_PRICE_PER_MILLION: float = settings.llm_output_price_per_million
EMBEDDING_TIMEOUT_SECONDS: float = settings.embedding_timeout_seconds
ROUTER_BACKEND: str = settings.router_backend
FINETUNED_ROUTER_URL: str = settings.finetuned_router_url

RETRIEVAL_HYBRID_CANDIDATE_K: int = settings.retrieval_hybrid_candidate_k
RETRIEVAL_HYBRID_BM25_WEIGHT: float = settings.retrieval_hybrid_bm25_weight
RETRIEVAL_HYBRID_DENSE_WEIGHT: float = settings.retrieval_hybrid_dense_weight

MAX_RETRIEVAL_RETRY: int = 1
MAX_GENERATION_RETRY: int = 1

FALLBACK_ANSWER: str = (
    "我无法仅根据当前检索到的上下文可靠回答这个问题。"
)

QA_PROMPT_VERSION: str = settings.qa_prompt_version

DEFAULT_SYSTEM_PROMPT: str = """
You are a RAG assistant.
Answer the user's question only based on the provided context.
If the context is not enough, say you do not know.
Keep the answer concise and accurate.
""".strip()
