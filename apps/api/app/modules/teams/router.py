import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.teams.schemas import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ApplicationOut,
    ApplicationPage,
    ApplicationStatus,
    ApplyRequest,
    MyApplicationPage,
    ProjectCreate,
    ProjectDetail,
    ProjectPage,
    ProjectUpdate,
)
from app.modules.teams.service import TeamService

router = APIRouter(tags=["teams"])

# A module-level singleton: ruff's B008 flags a `Query(...)` call used directly as a list-typed
# parameter default (the common FastAPI/Depends exception doesn't cover list types), so the
# instance is built once here and referenced by name instead.
STATUS_QUERY = Query(default=None, description="Repeat to select several statuses.")


def team_service(request: Request, db: AsyncSession = Depends(get_session)) -> TeamService:
    return TeamService(db, request.app.state.limiter)


@router.post("/projects", status_code=201)
async def create_project(
    body: ProjectCreate,
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ProjectDetail:
    return await service.create(current.user.id, body)


@router.get("/projects")
async def list_projects(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ProjectPage:
    return await service.list_projects(current.user.id, limit, offset)


@router.get("/projects/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ProjectDetail:
    return await service.detail(current.user.id, project_id)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ProjectDetail:
    """Owner only; the project row is locked for the whole update."""
    return await service.update(current.user.id, project_id, body)


@router.post("/projects/{project_id}/apply", status_code=201)
async def apply_to_project(
    project_id: uuid.UUID,
    body: ApplyRequest,
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ApplicationOut:
    """Applying again while one is pending returns that same application."""
    return await service.apply(current.user.id, project_id, body.role_id)


@router.get("/projects/{project_id}/applications")
async def list_applications(
    project_id: uuid.UUID,
    status: list[ApplicationStatus] | None = STATUS_QUERY,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ApplicationPage:
    """Owner only. `status` may repeat (`?status=a&status=b`) to select several at once."""
    return await service.applications(current.user.id, project_id, status, limit, offset)


@router.post("/projects/{project_id}/applications/{application_id}/accept")
async def accept_application(
    project_id: uuid.UUID,
    application_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ApplicationOut:
    """Owner only."""
    return await service.accept(current.user.id, project_id, application_id)


@router.post("/projects/{project_id}/applications/{application_id}/reject")
async def reject_application(
    project_id: uuid.UUID,
    application_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ApplicationOut:
    """Owner only."""
    return await service.reject(current.user.id, project_id, application_id)


@router.post("/projects/{project_id}/applications/{application_id}/withdraw", status_code=204)
async def withdraw_application(
    project_id: uuid.UUID,
    application_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> Response:
    """The applicant only; anyone else gets 404."""
    await service.withdraw(current.user.id, project_id, application_id)
    return Response(status_code=204)


@router.get("/me/projects")
async def list_my_projects(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> ProjectPage:
    """Projects the caller leads or is a member of, any status."""
    return await service.my_projects(current.user.id, limit, offset)


@router.get("/me/applications")
async def list_my_applications(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current: CurrentSession = Depends(require_verified),
    service: TeamService = Depends(team_service),
) -> MyApplicationPage:
    return await service.my_applications(current.user.id, limit, offset)
