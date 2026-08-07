from pathlib import Path

from rag_app.config import config
from rag_app.infrastructure.answer_cache import get_answer_cache
from rag_app.services.ingest_service import ingest_file


def get_supported_files() -> list[Path]:
    raw_data_dir = config.RAW_DATA_DIR
    files = [
        *raw_data_dir.glob("*.md"),
        *raw_data_dir.glob("*.pdf"),
    ]
    return sorted(files)


def build_index():
    supported_files = get_supported_files()
    total_documents = 0
    total_chunks = 0
    total_stored = 0
    for file_path in supported_files:
        result = ingest_file(str(file_path))

        total_documents += result["document_count"]
        total_chunks += result["chunk_count"]
        total_stored += result["stored_count"]

        print(
            f"indexed {result['path']} "
            f"documents={result['document_count']} "
            f"chunks={result['chunk_count']} "
            f"stored={result['stored_count']}"
        )

    answer_cache = get_answer_cache()
    if answer_cache is not None and total_stored > 0:
        answer_cache.bump_index_version()

    return {
        "file_count": len(supported_files),
        "document_count": total_documents,
        "chunk_count": total_chunks,
        "stored_count": total_stored,
    }


def main() -> None:
    result = build_index()

    print(
        f"done files={result['file_count']} "
        f"documents={result['document_count']} "
        f"chunks={result['chunk_count']} "
        f"stored={result['stored_count']}"
    )


if __name__ == "__main__":
    main()
