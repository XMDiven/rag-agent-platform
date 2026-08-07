from pathlib import Path
from unittest.mock import Mock

import pytest

from rag_app.config import config
from rag_app.scripts import build_index as build_index_module
from rag_app.scripts.build_index import build_index


def test_build_index_aggregates_markdown_and_pdf_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_data_dir = tmp_path / "raw"
    raw_data_dir.mkdir()

    markdown_file = raw_data_dir / "langchain-docs.md"
    pdf_file = raw_data_dir / "rag-paper.pdf"

    markdown_file.write_text(
        "# LangChain\nLangChain is a framework.",
        encoding="utf-8",
    )
    pdf_file.write_bytes(b"%PDF-1.4 fake pdf content")

    def fake_ingest_file(path: str) -> dict[str, str | int]:
        if path.endswith(".md"):
            return {
                "path": path,
                "document_count": 1,
                "chunk_count": 2,
                "stored_count": 2,
            }

        return {
            "path": path,
            "document_count": 1,
            "chunk_count": 3,
            "stored_count": 3,
        }

    mock_ingest_file = Mock(side_effect=fake_ingest_file)

    monkeypatch.setattr(config, "RAW_DATA_DIR", raw_data_dir)
    monkeypatch.setattr(
        build_index_module,
        "ingest_file",
        mock_ingest_file,
        raising=False,
    )

    result = build_index_module.build_index()

    assert result == {
        "file_count": 2,
        "document_count": 2,
        "chunk_count": 5,
        "stored_count": 5,
    }

    called_paths = {
        call.args[0]
        for call in mock_ingest_file.call_args_list
    }

    assert called_paths == {
        str(markdown_file),
        str(pdf_file),
    }


def test_build_index_aggregates_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    supported_files = [
        Path("data/raw/langchain-docs.md"),
        Path("data/raw/qdrant-docs.md"),
    ]

    mock_get_supported_files = Mock(return_value=supported_files)
    mock_ingest_file = Mock(
        side_effect=[
            {
                "path": "data/raw/langchain-docs.md",
                "document_count": 1,
                "chunk_count": 2,
                "stored_count": 2,
            },
            {
                "path": "data/raw/qdrant-docs.md",
                "document_count": 1,
                "chunk_count": 3,
                "stored_count": 3,
            },
        ]
    )
    answer_cache = Mock()

    monkeypatch.setattr(
        "rag_app.scripts.build_index.get_supported_files",
        mock_get_supported_files,
    )
    monkeypatch.setattr(
        "rag_app.scripts.build_index.ingest_file",
        mock_ingest_file,
    )
    monkeypatch.setattr(
        "rag_app.scripts.build_index.get_answer_cache",
        lambda: answer_cache,
    )

    result = build_index()

    assert result == {
        "file_count": 2,
        "document_count": 2,
        "chunk_count": 5,
        "stored_count": 5,
    }
    mock_get_supported_files.assert_called_once_with()
    assert mock_ingest_file.call_count == 2
    mock_ingest_file.assert_any_call("data/raw/langchain-docs.md")
    mock_ingest_file.assert_any_call("data/raw/qdrant-docs.md")
    answer_cache.bump_index_version.assert_called_once_with()
