from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.domain.projects import ModelSelection, ProjectInput

router = APIRouter()


@router.get("/projects")
def projects(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    archived: bool = False,
) -> dict:
    return request.app.state.projects.list(offset, limit, archived=archived)


@router.post("/projects", status_code=201)
def create_project(request: Request, body: ProjectInput) -> dict:
    return request.app.state.projects.save(body.name)


@router.patch("/projects/{identifier}")
def rename_project(request: Request, identifier: UUID, body: ProjectInput) -> dict:
    return request.app.state.projects.save(body.name, str(identifier))


@router.post("/projects/{identifier}/archive")
def archive_project(request: Request, identifier: UUID) -> dict:
    return request.app.state.projects.archive(str(identifier))


@router.post("/projects/{identifier}/restore")
def restore_project(request: Request, identifier: UUID) -> dict:
    return request.app.state.projects.restore(str(identifier))


@router.get("/model-catalog")
def model_catalog(request: Request) -> dict:
    return request.app.state.registry.catalog()


@router.put("/models/default")
def default_model(request: Request, body: ModelSelection) -> dict:
    return request.app.state.registry.set_default(body.connection_id, body.model)
