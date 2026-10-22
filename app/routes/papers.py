import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.core.errors import AppError
from app.domain.papers import MetadataPatch, MetricInput
from app.repositories.categories import CategoryRepository
from app.services.extraction_repair import refresh_extraction
from app.services.metadata import update_metadata, update_metric

router = APIRouter()


@router.get("/documents")
def list_documents(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    category_id: uuid.UUID | None = None,
    uncategorized: bool = False,
    include_samples: bool = True,
    q: str = Query("", max_length=200),
) -> dict:
    if category_id is not None and uncategorized:
        raise AppError("invalid_category_filter", "不能同时选择指定分类和未分类。", 422)
    if category_id:
        CategoryRepository(request.app.state.repository).require(str(category_id))
    items, total = request.app.state.repository.list(
        offset,
        limit,
        category_id=str(category_id) if category_id else None,
        uncategorized=uncategorized,
        include_samples=include_samples,
        search=q,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.post("/ingest")
@router.post("/documents", status_code=201)
async def upload(request: Request, file: Annotated[UploadFile, File()]) -> dict:
    settings = request.app.state.settings
    filename = file.filename or ""
    if (
        not filename.lower().endswith(".pdf")
        or len(filename) > 240
        or any(c in filename for c in ("/", "\\", ":", "\x00"))
        or any(ord(c) < 32 for c in filename)
    ):
        raise AppError("invalid_filename", "请上传文件名合法的 PDF，不允许目录路径。")
    incoming = settings.data_dir / "incoming"
    incoming.mkdir(exist_ok=True)
    temporary = incoming / f"{uuid.uuid4()}.pdf"
    try:
        size = 0
        with temporary.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise AppError("upload_limit", "文件超过上传大小限制。", 413)
                target.write(chunk)
        document, duplicate = await run_in_threadpool(
            request.app.state.ingestion.ingest, temporary, filename
        )
        return {"document": document, "duplicate": duplicate}
    finally:
        temporary.unlink(missing_ok=True)
        await file.close()


@router.get("/documents/{document_id}")
def get_document(request: Request, document_id: uuid.UUID) -> object:
    return request.app.state.repository.get(str(document_id))


@router.post("/documents/{document_id}/retry")
def retry_document(request: Request, document_id: uuid.UUID) -> object:
    return request.app.state.ingestion.retry(str(document_id))


@router.patch("/documents/{document_id}/metadata")
def edit_metadata(request: Request, document_id: uuid.UUID, patch: MetadataPatch) -> object:
    return update_metadata(request.app.state.repository, str(document_id), patch)


@router.post("/documents/{document_id}/refresh-extraction")
def refresh_document(request: Request, document_id: uuid.UUID) -> object:
    return refresh_extraction(request.app.state.repository, str(document_id))


@router.post("/documents/{document_id}/reparse")
def reparse_document(request: Request, document_id: uuid.UUID) -> object:
    return request.app.state.ingestion.reprocess(str(document_id))


@router.put("/documents/{document_id}/metric")
def edit_metric(request: Request, document_id: uuid.UUID, patch: MetricInput) -> object:
    return update_metric(request.app.state.repository, str(document_id), patch)


@router.get("/documents/{document_id}/original")
def original(request: Request, document_id: uuid.UUID) -> FileResponse:
    document = request.app.state.repository.get(str(document_id))
    path = request.app.state.settings.data_dir / "papers" / str(document_id) / "original.pdf"
    if not path.is_file():
        raise AppError("asset_missing", "原始文件缺失，请检查备份。", 404)
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=document.filename,
        content_disposition_type="inline",
    )


@router.get("/documents/{document_id}/assets/{asset:path}")
def asset_file(request: Request, document_id: uuid.UUID, asset: str) -> FileResponse:
    document = request.app.state.repository.get(str(document_id))
    allowed: set[str] = set()
    if document.parsed:
        allowed.update(p.image for p in document.parsed.pages)
        allowed.update(f.image for f in document.parsed.figures)
        allowed.update(t.image for t in document.parsed.tables if t.image)
        allowed.update(e.image for e in document.parsed.equations)
    if asset not in allowed:
        raise AppError("asset_not_found", "图片不存在。", 404)
    directory: Path = request.app.state.settings.data_dir / "papers" / str(document_id)
    target = (directory / asset).resolve()
    if not target.is_relative_to(directory.resolve()) or not target.is_file():
        raise AppError("asset_missing", "图片文件缺失。", 404)
    return FileResponse(target, media_type="image/png")


@router.get("/documents/{document_id}/exports/{kind}")
def export_file(request: Request, document_id: uuid.UUID, kind: str) -> FileResponse:
    document = request.app.state.repository.get(str(document_id))
    formats = {
        "docling_json": ("application/json", "structure.json"),
        "markdown": ("text/markdown", "paper.md"),
    }
    if kind not in formats or not document.parsed or kind not in document.parsed.exports:
        raise AppError("export_missing", "没有此结构化导出，请先重新解析。", 404)
    directory = request.app.state.settings.data_dir / "papers" / str(document_id)
    path = (directory / document.parsed.exports[kind]).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise AppError("export_missing", "结构化文件缺失。", 404)
    media, filename = formats[kind]
    return FileResponse(path, media_type=media, filename=filename)
