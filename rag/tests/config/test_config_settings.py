import pytest
from pydantic import ValidationError

from rag_app.config.config import Settings


def test_settings_uses_defaults_without_env_file(monkeypatch) -> None:
    monkeypatch.delenv("RETRIEVAL_TOP_K", raising=False)
    monkeypatch.delenv("RETRIEVAL_SEARCH_TYPE", raising=False)
    monkeypatch.delenv("RETRIEVAL_FETCH_K", raising=False)
    monkeypatch.delenv("RETRIEVAL_LAMBDA_MULT", raising=False)

    settings = Settings(_env_file=None)

    assert settings.retrieval_top_k == 7
    assert settings.retrieval_search_type == "similarity"
    assert settings.retrieval_fetch_k == 50
    assert settings.retrieval_lambda_mult == 0.3


def test_settings_reads_environment_variables(monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_TOP_K", "3")
    monkeypatch.setenv("RETRIEVAL_SEARCH_TYPE", "mmr")
    monkeypatch.setenv("RETRIEVAL_FETCH_K", "20")
    monkeypatch.setenv("RETRIEVAL_LAMBDA_MULT", "0.5")
    monkeypatch.setenv("QDRANT_COLLECTION", "documents")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("ANSWER_CACHE_TTL_SECONDS", "600")

    settings = Settings(_env_file=None)

    assert settings.retrieval_top_k == 3
    assert settings.retrieval_search_type == "mmr"
    assert settings.retrieval_fetch_k == 20
    assert settings.retrieval_lambda_mult == 0.5
    assert settings.qdrant_collection == "documents"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.answer_cache_ttl_seconds == 600


def test_settings_reads_llm_thinking_type(monkeypatch) -> None:
    monkeypatch.setenv("LLM_THINKING_TYPE", "disabled")

    settings = Settings(_env_file=None)

    assert settings.llm_thinking_type == "disabled"


def test_settings_reads_hybrid_retrieval_search_type(monkeypatch) -> None:
    monkeypatch.setenv("RETRIEVAL_SEARCH_TYPE", "hybrid")

    settings = Settings(_env_file=None)

    assert settings.retrieval_search_type == "hybrid"


def test_settings_rejects_unsupported_retrieval_search_type(
    monkeypatch,
) -> None:
    monkeypatch.setenv("RETRIEVAL_SEARCH_TYPE", "rerank")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_prefers_moonshot_key_over_openai_key(monkeypatch) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "moonshot-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    settings = Settings(_env_file=None)

    assert settings.llm_api_key == "moonshot-key"


def test_settings_falls_back_to_openai_key(monkeypatch) -> None:
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    settings = Settings(_env_file=None)

    assert settings.llm_api_key == "openai-key"
