from uuid import UUID

from fastapi import APIRouter, Request

from app.domain.categories import CategoryInput, MovePapers
from app.repositories.categories import CategoryRepository

router = APIRouter()


def repository(request: Request) -> CategoryRepository:
    return CategoryRepository(request.app.state.repository)


@router.get("/categories")
def list_categories(
    request: Request, include_archived: bool = False, include_samples: bool = True
) -> dict:
    return repository(request).list(
        include_archived=include_archived, include_samples=include_samples
    )


@router.post("/categories", status_code=201)
def create_category(request: Request, body: CategoryInput) -> object:
    return repository(request).create(body.name)


@router.patch("/categories/{identifier}")
def rename_category(request: Request, identifier: UUID, body: CategoryInput) -> object:
    return repository(request).rename(str(identifier), body.name)


@router.post("/categories/{identifier}/archive")
def archive_category(request: Request, identifier: UUID) -> object:
    return repository(request).archive(str(identifier), archived=True)


@router.post("/categories/{identifier}/restore")
def restore_category(request: Request, identifier: UUID) -> object:
    return repository(request).archive(str(identifier), archived=False)


@router.post("/documents/move")
def move_documents(request: Request, body: MovePapers) -> dict:
    return repository(request).move(
        [str(value) for value in body.document_ids],
        str(body.category_id) if body.category_id else None,
    )
