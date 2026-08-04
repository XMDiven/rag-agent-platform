from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from rag_app.config import config
from rag_app.schemas.document_schema import (
    DocumentIngestRequest,
    DocumentIngestResponse,
)
from rag_app.services.ingest_service import ingest_file

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".md", ".pdf"}


def resolve_uploaded_document_path(filename: str) -> Path:
    safe_filename = Path(filename or "").name
    suffix = Path(safe_filename).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only .md and .pdf files are supported",
        )

    document_path = config.RAW_DATA_DIR / safe_filename

    if not document_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return document_path


def write_uploaded_bytes(saved_path: Path, content: bytes) -> None:
    saved_path.parent.mkdir(parents=True, exist_ok=True)
    saved_path.write_bytes(content)


async def save_upload_file(file: UploadFile, filename: str) -> dict[str, str]:
    saved_path = config.RAW_DATA_DIR / filename

    content = await file.read()
    # 同步写盘会阻塞事件循环，一个上百页 PDF 会让整个进程的其他请求全部停摆。
    await run_in_threadpool(write_uploaded_bytes, saved_path, content)

    return {
        "filename": filename,
        "saved_path": str(saved_path.relative_to(config.RAW_DATA_DIR)),
        "content_type": file.content_type or "unknown",
    }


def validate_upload_file(file: UploadFile) -> str:
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()

    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only .md and .pdf files are supported",
        )

    return filename


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)) -> dict[str, str]:
    filename = validate_upload_file(file)
    return await save_upload_file(file, filename)


@router.post("/upload/batch")
async def upload_documents(
    files: list[UploadFile] = File(...),
) -> dict[str, list[dict[str, str]]]:
    filenames = [validate_upload_file(file) for file in files]

    saved_files = []
    for file, filename in zip(files, filenames, strict=True):
        saved_files.append(await save_upload_file(file, filename))

    return {
        "files": saved_files,
    }


@router.post("/ingest")
def ingest_document(
    body: DocumentIngestRequest,
    request: Request,
) -> DocumentIngestResponse:
    document_path = resolve_uploaded_document_path(body.filename)
    result = ingest_file(
        path=str(document_path),
        resources=request.app.state.resources,
    )
    return DocumentIngestResponse(**result)
