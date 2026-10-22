import hashlib
import logging
import threading
import uuid
from pathlib import Path
from typing import Protocol

from pypdf import PdfReader

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.papers import Document, ParsedPaper, utc_now
from app.repositories.papers import PaperRepository
from app.services.parse_assets import prefix_assets

logger = logging.getLogger(__name__)


class Parser(Protocol):
    def parse(self, source: Path, assets: Path) -> ParsedPaper: ...


class IngestionService:
    def __init__(self, settings: Settings, repository: PaperRepository, parser: Parser) -> None:
        self.settings = settings
        self.repository = repository
        self.parser = parser
        self.lock = threading.Lock()

    def ingest(self, source: Path, filename: str) -> tuple[Document, bool]:
        if not self.lock.acquire(blocking=False):
            raise AppError("ingestion_busy", "正在解析另一篇论文，请稍后再试。", 409)
        try:
            with source.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            existing = self.repository.by_hash(digest)
            if existing:
                return existing, True
            self._validate_pdf(source)
            document_id = str(uuid.uuid4())
            directory = self.settings.data_dir / "papers" / document_id
            directory.mkdir(parents=True)
            source.replace(directory / "original.pdf")
            document = Document(
                id=document_id,
                filename=filename,
                sha256=digest,
                status="parsing",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.repository.save(document)
            return self._parse(document), False
        finally:
            self.lock.release()

    def retry(self, document_id: str) -> Document:
        if not self.lock.acquire(blocking=False):
            raise AppError("ingestion_busy", "正在解析另一篇论文，请稍后再试。", 409)
        try:
            document = self.repository.get(document_id)
            if document.status != "failed":
                raise AppError("not_retryable", "只有解析失败的论文可以重试。", 409)
            document.status = "parsing"
            document.error_code = document.error_message = None
            self.repository.save(document)
            return self._parse(document)
        finally:
            self.lock.release()

    def _validate_pdf(self, source: Path) -> None:
        with source.open("rb") as stream:
            header = stream.read(1024)
        if not header.lstrip().startswith(b"%PDF-"):
            raise AppError("invalid_pdf", "文件内容不是有效 PDF。")
        try:
            reader = PdfReader(source)
            if reader.is_encrypted:
                raise AppError("encrypted_pdf", "请先解除 PDF 加密后上传。")
            if not reader.pages:
                raise AppError("empty_pdf", "PDF 没有页面。")
            if len(reader.pages) > self.settings.max_pages:
                raise AppError("page_limit", f"PDF 超过 {self.settings.max_pages} 页限制。", 413)
            if not any(page.extract_text().strip() for page in reader.pages):
                raise AppError("no_text", "没有可提取文字，扫描论文需要 OCR。")
        except AppError:
            raise
        except Exception:
            raise AppError("invalid_pdf", "PDF 已损坏或无法读取。") from None

    def reprocess(self, document_id: str) -> Document:
        from app.services.reprocessing import reprocess

        if not self.lock.acquire(blocking=False):
            raise AppError("ingestion_busy", "正在解析另一篇论文，请稍后再试。", 409)
        try:
            return reprocess(self.repository, self.parser, self.settings.data_dir, document_id)
        finally:
            self.lock.release()

    def _parse(self, document: Document) -> Document:
        directory = self.settings.data_dir / "papers" / document.id
        assets = directory / str(uuid.uuid4())
        assets.mkdir()
        try:
            paper = self.parser.parse(directory / "original.pdf", assets)
            # Asset names are internal, and versioned so a failed retry cannot replace valid assets.
            prefix_assets(paper, assets.name)
            document.parsed = paper
            document.status = "parsed"
            document.error_code = document.error_message = None
        except AppError as error:
            document.status = "failed"
            document.error_code, document.error_message = error.code, error.message
        except Exception as error:
            logger.error("parser_failure document_id=%s type=%s", document.id, type(error).__name__)
            document.status = "failed"
            document.error_code = "parser_failed"
            document.error_message = "解析失败，可检查模型安装后重试。"
        self.repository.save(document)
        return document
