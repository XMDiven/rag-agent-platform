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

    embedding_base_url: str | None = None
    embedding_model: str | None = None

    llm_base_url: str | None = None
    llm_model_id: str | None = None
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
