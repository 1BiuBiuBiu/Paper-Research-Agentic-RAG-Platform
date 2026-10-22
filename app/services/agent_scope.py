from app.core.errors import AppError
from app.domain.agent import AgentScope
from app.domain.papers import Document
from app.repositories.categories import CategoryRepository
from app.repositories.papers import PaperRepository


class ScopeResolver:
    def __init__(self, papers: PaperRepository) -> None:
        self.papers = papers

    def resolve(self, scope: AgentScope) -> list[Document]:
        if scope.kind == "chat":
            return []
        ids = {str(i) for i in scope.document_ids}
        folders = {str(i) for i in scope.folder_ids}
        if scope.folder_id:
            folders.add(str(scope.folder_id))
        categories = CategoryRepository(self.papers)
        for identifier in sorted(folders):
            categories.require(identifier)
        excluded = {str(i) for i in scope.excluded_document_ids}
        ids.difference_update(excluded)
        targets: list[str | None] = sorted(folders) + ([None] if scope.uncategorized else [])
        if scope.kind == "all":
            targets = [None]
        for folder in targets:
            offset = 0
            while True:
                rows, count = self.papers.list(
                    offset=offset,
                    limit=100,
                    category_id=folder,
                    uncategorized=folder is None and scope.kind != "all",
                    include_samples=scope.include_samples,
                )
                ids.update(row.id for row in rows if row.id not in excluded)
                if len(ids) > 100:
                    raise AppError(
                        "scope_limit", "所选资料合并去重后超过 100 篇，请减少论文或文件夹。", 422
                    )
                offset += len(rows)
                if offset >= count or not rows:
                    break
        if not ids:
            raise AppError("empty_scope", "当前资料范围没有可用文献，请上传论文或重新选择。", 422)
        documents = [self.papers.get(i) for i in sorted(ids)]
        if not scope.include_samples and any(d.is_sample for d in documents):
            raise AppError("sample_scope", "演示样本需要显式启用。", 422)
        return documents
