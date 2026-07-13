from unittest.mock import Mock

from qdrant_client import models

from rag_app.scripts import ensure_collection


def configure_qdrant(monkeypatch) -> None:
    monkeypatch.setattr(
        ensure_collection.config.settings,
        "qdrant_url",
        "http://localhost:6333",
    )
    monkeypatch.setattr(
        ensure_collection.config.settings,
        "qdrant_collection",
        "documents",
    )


def test_ensure_collection_keeps_existing_collection(monkeypatch) -> None:
    configure_qdrant(monkeypatch)
    mock_client = Mock()
    mock_client.collection_exists.return_value = True
    monkeypatch.setattr(
        ensure_collection,
        "QdrantClient",
        Mock(return_value=mock_client),
    )
    mock_get_embeddings = Mock()
    monkeypatch.setattr(
        ensure_collection,
        "get_embeddings",
        mock_get_embeddings,
    )

    result = ensure_collection.ensure_collection()

    assert result == {"collection": "documents", "created": False}
    mock_client.collection_exists.assert_called_once_with("documents")
    mock_client.create_collection.assert_not_called()
    mock_get_embeddings.assert_not_called()


def test_ensure_collection_creates_missing_collection(monkeypatch) -> None:
    configure_qdrant(monkeypatch)
    mock_client = Mock()
    mock_client.collection_exists.return_value = False
    monkeypatch.setattr(
        ensure_collection,
        "QdrantClient",
        Mock(return_value=mock_client),
    )
    mock_embeddings = Mock()
    mock_embeddings.embed_query.return_value = [0.1, 0.2, 0.3]
    monkeypatch.setattr(
        ensure_collection,
        "get_embeddings",
        Mock(return_value=mock_embeddings),
    )

    result = ensure_collection.ensure_collection()

    assert result == {"collection": "documents", "created": True}
    mock_embeddings.embed_query.assert_called_once_with("dimension probe")
    mock_client.create_collection.assert_called_once_with(
        collection_name="documents",
        vectors_config=models.VectorParams(
            size=3,
            distance=models.Distance.COSINE,
        ),
    )
