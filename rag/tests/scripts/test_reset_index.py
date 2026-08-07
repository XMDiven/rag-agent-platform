from unittest.mock import Mock

from qdrant_client import models

from rag_app.scripts import reset_index


def test_reset_index_deletes_all_points_from_configured_collection(
    monkeypatch,
) -> None:
    """Delete all points from the configured Qdrant collection."""
    mock_client = Mock()
    mock_qdrant_client = Mock(return_value=mock_client)
    answer_cache = Mock()

    monkeypatch.setattr(
        reset_index.config.settings,
        "qdrant_url",
        "http://localhost:6333",
    )
    monkeypatch.setattr(
        reset_index.config.settings,
        "qdrant_collection",
        "documents",
    )
    monkeypatch.setattr(reset_index, "QdrantClient", mock_qdrant_client)
    monkeypatch.setattr(
        reset_index,
        "get_answer_cache",
        lambda: answer_cache,
    )

    result = reset_index.reset_index()

    assert result == {"collection": "documents", "deleted": True}
    mock_qdrant_client.assert_called_once_with(url="http://localhost:6333")
    mock_client.delete.assert_called_once_with(
        collection_name="documents",
        points_selector=models.Filter(must=[]),
        wait=True,
    )
    answer_cache.bump_index_version.assert_called_once_with()
