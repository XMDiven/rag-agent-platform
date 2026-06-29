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
