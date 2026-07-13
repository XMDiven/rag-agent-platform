from qdrant_client import QdrantClient, models

from rag_app.config import config
from rag_app.infrastructure.embedding_client import get_embeddings


def ensure_collection() -> dict[str, str | bool]:
    """Create the configured collection only when it does not exist."""
    qdrant_url = config.settings.qdrant_url
    collection_name = config.settings.qdrant_collection

    if not qdrant_url:
        raise RuntimeError("QDRANT_URL is not set")

    if not collection_name:
        raise RuntimeError("QDRANT_COLLECTION is not set")

    client = QdrantClient(url=qdrant_url)
    if client.collection_exists(collection_name):
        return {"collection": collection_name, "created": False}

    vector_size = len(get_embeddings().embed_query("dimension probe"))
    if vector_size == 0:
        raise RuntimeError("Embedding model returned an empty vector")

    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=vector_size,
            distance=models.Distance.COSINE,
        ),
    )

    return {"collection": collection_name, "created": True}


def main() -> None:
    result = ensure_collection()
    print(
        f"ensure collection={result['collection']} created={result['created']}"
    )


if __name__ == "__main__":
    main()
