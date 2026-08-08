from rag_app.config.config import Settings
from rag_app.infrastructure import llm_client


def test_get_client_passes_thinking_config_to_chat_openai(monkeypatch) -> None:
    captured_kwargs = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "kimi-k2.6")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_THINKING_TYPE", "disabled")
    monkeypatch.setattr(llm_client.config, "settings", Settings(_env_file=None))
    monkeypatch.setattr(llm_client, "ChatOpenAI", FakeChatOpenAI)

    llm_client.get_client()

    assert captured_kwargs["model"] == "kimi-k2.6"
    assert captured_kwargs["extra_body"] == {
        "thinking": {
            "type": "disabled",
        },
    }


def test_get_client_bounds_the_upstream_wait(monkeypatch) -> None:
    """无超时的调用会永久占住线程池里的一个线程。"""
    captured_kwargs = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL_ID", "kimi-k2.6")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm_client.config, "settings", Settings(_env_file=None))
    monkeypatch.setattr(
        llm_client.config,
        "LLM_TIMEOUT_SECONDS",
        60.0,
    )
    monkeypatch.setattr(llm_client.config, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(llm_client, "ChatOpenAI", FakeChatOpenAI)

    llm_client.get_client()

    assert captured_kwargs["timeout"] == 60.0
    assert captured_kwargs["max_retries"] == 1
