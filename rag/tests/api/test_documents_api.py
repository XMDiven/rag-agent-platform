import asyncio
import inspect
from unittest.mock import Mock

from rag_app.app.routers import documents as documents_router
from rag_app.config import config
from rag_app.infrastructure.resources import AppResources


def test_ingest_route_handler_is_sync() -> None:
    assert not inspect.iscoroutinefunction(documents_router.ingest_document)


def test_upload_markdown_document(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path.parent)

    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "example.md",
                b"# Example\n\nHello RAG",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "example.md",
        "saved_path": "example.md",
        "content_type": "text/markdown",
    }
    assert (tmp_path / "example.md").read_bytes() == b"# Example\n\nHello RAG"


def test_upload_rejects_unsupported_file_type(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path.parent)

    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "example.txt",
                b"not supported",
                "text/plain",
            )
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Only .md and .pdf files are supported"
    }


def test_upload_uses_safe_filename(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)

    response = client.post(
        "/documents/upload",
        files={
            "file": (
                "../example.md",
                b"# Safe filename",
                "text/markdown",
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["filename"] == "example.md"
    assert response.json()["saved_path"] == "example.md"
    assert (tmp_path / "example.md").read_bytes() == b"# Safe filename"
    assert not (tmp_path.parent / "example.md").exists()


def test_upload_batch_documents(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)

    response = client.post(
        "/documents/upload/batch",
        files=[
            (
                "files",
                (
                    "one.md",
                    b"# One",
                    "text/markdown",
                ),
            ),
            (
                "files",
                (
                    "two.pdf",
                    b"%PDF-1.4",
                    "application/pdf",
                ),
            ),
        ],
    )

    assert response.status_code == 200
    assert response.json() == {
        "files": [
            {
                "filename": "one.md",
                "saved_path": "one.md",
                "content_type": "text/markdown",
            },
            {
                "filename": "two.pdf",
                "saved_path": "two.pdf",
                "content_type": "application/pdf",
            },
        ]
    }
    assert (tmp_path / "one.md").read_bytes() == b"# One"
    assert (tmp_path / "two.pdf").read_bytes() == b"%PDF-1.4"


def test_upload_batch_rejects_unsupported_file_type(
    client,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)

    response = client.post(
        "/documents/upload/batch",
        files=[
            (
                "files",
                (
                    "valid.md",
                    b"# Valid",
                    "text/markdown",
                ),
            ),
            (
                "files",
                (
                    "invalid.txt",
                    b"not supported",
                    "text/plain",
                ),
            ),
        ],
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Only .md and .pdf files are supported"
    }
    assert not (tmp_path / "valid.md").exists()
    assert not (tmp_path / "invalid.txt").exists()


def test_ingest_uploaded_markdown_document(
    client,
    tmp_path,
    monkeypatch,
    app_resources: AppResources,
) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
    saved_file = tmp_path / "example.md"
    saved_file.write_text("# Example", encoding="utf-8")

    expected_result = {
        "path": str(saved_file),
        "document_count": 1,
        "chunk_count": 2,
        "stored_count": 2,
    }

    def fake_ingest_file(
        path: str,
        resources: AppResources | None = None,
    ) -> dict[str, str | int]:
        assert path == str(saved_file)
        assert resources is app_resources
        return expected_result

    monkeypatch.setattr(
        "rag_app.app.routers.documents.ingest_file",
        fake_ingest_file,
    )
    answer_cache = Mock()
    monkeypatch.setattr(
        "rag_app.app.routers.documents.get_answer_cache",
        lambda: answer_cache,
    )

    response = client.post(
        "/documents/ingest",
        json={"filename": "example.md"},
    )

    assert response.status_code == 200
    assert response.json() == expected_result
    answer_cache.bump_index_version.assert_called_once_with()


def test_ingest_rejects_missing_document(
    client,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)

    response = client.post(
        "/documents/ingest",
        json={"filename": "missing.md"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_ingest_rejects_unsupported_file_type(
    client,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
    (tmp_path / "example.txt").write_text("not supported", encoding="utf-8")

    response = client.post(
        "/documents/ingest",
        json={"filename": "example.txt"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "detail": "Only .md and .pdf files are supported"
    }


def test_ingest_uses_safe_filename(
    client,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
    unsafe_parent_file = tmp_path.parent / "secret.md"
    unsafe_parent_file.write_text("# Secret", encoding="utf-8")

    response = client.post(
        "/documents/ingest",
        json={"filename": "../secret.md"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


def test_upload_writes_the_file_off_the_event_loop(client, tmp_path, monkeypatch) -> None:
    """同步写盘必须留在线程池里，否则大文件会卡住整个事件循环。

    判据是写盘时能否拿到运行中的事件循环：拿得到说明它跑在 loop 线程上，
    也就是阻塞了所有其他请求。
    """
    monkeypatch.setattr(config, "RAW_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path.parent)

    ran_on_event_loop: list[bool] = []
    original_write = documents_router.write_uploaded_bytes

    def recording_write(saved_path, content):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            ran_on_event_loop.append(False)
        else:
            ran_on_event_loop.append(True)
        original_write(saved_path, content)

    monkeypatch.setattr(
        documents_router,
        "write_uploaded_bytes",
        recording_write,
    )

    response = client.post(
        "/documents/upload",
        files={"file": ("example.md", b"# Example", "text/markdown")},
    )

    assert response.status_code == 200
    assert ran_on_event_loop == [False]
