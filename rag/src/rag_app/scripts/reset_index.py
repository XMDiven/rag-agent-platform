from qdrant_client import QdrantClient, models

from rag_app.config import config
from rag_app.infrastructure.answer_cache import get_answer_cache


def reset_index() -> dict[str, str | bool]:
    """Delete all points from the configured Qdrant collection."""
    qdrant_url = config.settings.qdrant_url
    collection_name = config.settings.qdrant_collection

    if not qdrant_url:
        raise RuntimeError("QDRANT_URL is not set")

    if not collection_name:
        raise RuntimeError("QDRANT_COLLECTION is not set")

    client = QdrantClient(url=qdrant_url)
    client.delete(
        collection_name=collection_name,
        points_selector=models.Filter(must=[]),
        wait=True,
    )
    answer_cache = get_answer_cache()
    if answer_cache is not None:
        answer_cache.bump_index_version()

    return {
        "collection": collection_name,
        "deleted": True,
    }


def main() -> None:
    """Run the index reset script."""
    result = reset_index()
    print(f"reset collection={result['collection']} deleted={result['deleted']}")


if __name__ == "__main__":
    main()
