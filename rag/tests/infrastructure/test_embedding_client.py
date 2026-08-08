from rag_app.config.config import Settings
from rag_app.infrastructure import embedding_client


def test_get_embeddings_bounds_the_upstream_wait(monkeypatch) -> None:
    """embedding 每次 /ask 都会调用，无超时同样会泄漏线程。"""
    captured_kwargs = {}

    class FakeOllamaEmbeddings:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3:latest")
    monkeypatch.setattr(
        embedding_client.config,
        "settings",
        Settings(_env_file=None),
    )
    monkeypatch.setattr(
        embedding_client.config,
        "EMBEDDING_TIMEOUT_SECONDS",
        30.0,
    )
    monkeypatch.setattr(
        embedding_client,
        "OllamaEmbeddings",
        FakeOllamaEmbeddings,
    )

    embedding_client.get_embeddings()

    assert captured_kwargs["client_kwargs"] == {"timeout": 30.0}
