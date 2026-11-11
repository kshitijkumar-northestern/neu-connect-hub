import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Literal, cast

from sqlalchemy import Row, Select, case, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.modules.chat.models import Conversation, ConversationMember
from app.modules.profiles.models import Profile, ProfileSkill, Skill
from app.modules.profiles.schemas import NamedItem
from app.modules.profiles.visibility import blocked_between
from app.modules.teams.models import (
    PENDING_SQL,
    Application,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectSkill,
)
from app.modules.teams.schemas import (
    Applicant,
    ApplicationOut,
    ApplicationPage,
    ApplicationStatus,
    Member,
    MyApplicationOut,
    MyApplicationPage,
    Owner,
    ProjectCreate,
    ProjectDetail,
    ProjectPage,
    ProjectRef,
    ProjectStatus,
    ProjectSummary,
    ProjectUpdate,
    RoleDetail,
    RoleRef,
    RoleSummary,
    RoleUpdate,
    ViewerApplication,
)

CREATE_LIMIT = Limit(max_events=5, window_seconds=3600)
APPLY_LIMIT = Limit(max_events=20, window_seconds=3600)

NOT_FOUND = "Project not found."
FORBIDDEN = "Only the project's owner can do this."
APPLICATION_NOT_FOUND = "Application not found."
UNKNOWN_ROLE = "That role isn't part of this project."
UNKNOWN_SKILL = "One of the skills isn't in the list. Choose from the list."
CAPACITY_TOO_LOW = "Capacity can't be lower than the current team size."
INVALID_STATUS = "Only the server sets a project to full."
CAPACITY_REACHED = "The project is already at capacity."
ROLE_OPEN_SLOTS_TOO_LOW = "Open slots can't be lower than the role's current holders."
CANNOT_REMOVE_ROLE_IN_USE = "This role has a member or a pending application; it can't be removed."
CANNOT_APPLY_TO_OWN_PROJECT = "You can't apply to your own project."
ALREADY_A_MEMBER = "You're already on this team."
PROJECT_CLOSED = "This project isn't taking applications."
PROJECT_FULL = "This project is full."
ROLE_FULL = "This role is full."
APPLICATION_NOT_PENDING = "This application has already been decided."


class TeamService:
    """Projects, roles, teams and applications. Status transitions and capacity checks run
    under a row lock on the project so they never race an accept or another edit."""

    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db = db
        self.limiter = limiter

    # --- create, list, detail ----------------------------------------------------------------

    async def create(self, owner_id: uuid.UUID, data: ProjectCreate) -> ProjectDetail:
        self._limit_create(owner_id)
        skill_ids = await self._skill_ids({slug for role in data.roles for slug in role.skills})

        status: ProjectStatus = "full" if data.capacity <= 1 else "open"
        project = Project(
            owner_id=owner_id,
            title=data.title,
            summary=data.summary,
            capacity=data.capacity,
            status=status,
        )
        self.db.add(project)
        await self.db.flush()
        self.db.add(ProjectMember(project_id=project.id, user_id=owner_id, member_role="owner"))

        for role_input in data.roles:
            role = ProjectRole(
                project_id=project.id, title=role_input.title, open_slots=role_input.open_slots
            )
            self.db.add(role)
            await self.db.flush()
            if role_input.skills:
                await self.db.execute(
                    insert(ProjectSkill),
                    [
                        {"role_id": role.id, "skill_id": skill_ids[slug]}
                        for slug in role_input.skills
                    ],
                )

        conversation = Conversation(kind="project", project_id=project.id)
        self.db.add(conversation)
        await self.db.flush()
        self.db.add(ConversationMember(conversation_id=conversation.id, user_id=owner_id))
        await self.db.commit()
        return await self.detail(owner_id, project.id)

    async def list_projects(self, viewer_id: uuid.UUID, limit: int, offset: int) -> ProjectPage:
        rows = (
            await self.db.execute(
                select(Project, Profile.display_name)
                .outerjoin(Profile, Profile.user_id == Project.owner_id)
                .where(Project.status == "open", ~blocked_between(Project.owner_id, viewer_id))
                .order_by(Project.created_at.desc(), Project.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return await self._summary_page(rows, limit, offset)

    async def my_projects(self, viewer_id: uuid.UUID, limit: int, offset: int) -> ProjectPage:
        """Projects the viewer leads or is an ordinary member of, any status: the only way to
        find a full or closed project again once it has fallen out of the public list."""
        rows = (
            await self.db.execute(
                select(Project, Profile.display_name)
                .join(ProjectMember, ProjectMember.project_id == Project.id)
                .outerjoin(Profile, Profile.user_id == Project.owner_id)
                .where(ProjectMember.user_id == viewer_id)
                .order_by(Project.created_at.desc(), Project.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        return await self._summary_page(rows, limit, offset)

    async def _summary_page(
        self, rows: Sequence[Row[Project, str]], limit: int, offset: int
    ) -> ProjectPage:
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        project_ids = [project.id for project, _ in page_rows]
        member_counts = await self._member_counts(project_ids)
        roles_by_project = await self._role_summaries(project_ids)

        items = [
            ProjectSummary(
                id=project.id,
                title=project.title,
                summary=project.summary,
                capacity=project.capacity,
                status=cast(ProjectStatus, project.status),
                member_count=member_counts.get(project.id, 0),
                owner=Owner(user_id=project.owner_id, display_name=name),
                roles=roles_by_project.get(project.id, []),
                created_at=project.created_at,
            )
            for project, name in page_rows
        ]
        return ProjectPage(items=items, next_offset=offset + limit if has_more else None)

    async def detail(self, viewer_id: uuid.UUID, project_id: uuid.UUID) -> ProjectDetail:
        row = (
            await self.db.execute(
                select(Project, Profile.display_name)
                .outerjoin(Profile, Profile.user_id == Project.owner_id)
                .where(Project.id == project_id, ~blocked_between(Project.owner_id, viewer_id))
            )
        ).first()
        if row is None:
            raise AppError(404, "not_found", NOT_FOUND)
        project, owner_name = row

        roles = list(
            await self.db.scalars(
                select(ProjectRole)
                .where(ProjectRole.project_id == project_id)
                .order_by(ProjectRole.created_at, ProjectRole.id)
            )
        )
        role_ids = [role.id for role in roles]
        skill_rows = (
            (
                await self.db.execute(
                    select(ProjectSkill.role_id, Skill.id, Skill.slug, Skill.name)
                    .join(Skill, Skill.id == ProjectSkill.skill_id)
                    .where(ProjectSkill.role_id.in_(role_ids))
                )
            ).all()
            if role_ids
            else []
        )
        skills_by_role: dict[uuid.UUID, list[tuple[int, str, str]]] = defaultdict(list)
        for role_id, skill_id, slug, name in skill_rows:
            skills_by_role[role_id].append((skill_id, slug, name))

        member_rows = (
            await self.db.execute(
                select(
                    ProjectMember.user_id,
                    ProjectMember.role_id,
                    ProjectMember.member_role,
                    ProjectMember.joined_at,
                    Profile.display_name,
                    blocked_between(ProjectMember.user_id, viewer_id).label("blocked"),
                )
                .outerjoin(Profile, Profile.user_id == ProjectMember.user_id)
                .where(ProjectMember.project_id == project_id)
                .order_by(
                    case((ProjectMember.member_role == "owner", 0), else_=1),
                    ProjectMember.joined_at,
                )
            )
        ).all()
        member_count = len(member_rows)
        holders: Counter[uuid.UUID] = Counter(
            row.role_id for row in member_rows if row.role_id is not None
        )
        role_titles = {role.id: role.title for role in roles}
        remaining = {role.id: max(0, role.open_slots - holders.get(role.id, 0)) for role in roles}

        role_out = [
            RoleDetail(
                id=role.id,
                title=role.title,
                open_slots=role.open_slots,
                remaining_slots=remaining[role.id],
                skills=sorted(
                    (
                        NamedItem(slug=slug, name=name)
                        for _, slug, name in skills_by_role.get(role.id, [])
                    ),
                    key=lambda item: item.name.casefold(),
                ),
            )
            for role in roles
        ]

        has_skill_ids = set(
            await self.db.scalars(
                select(ProfileSkill.skill_id)
                .join(ProjectMember, ProjectMember.user_id == ProfileSkill.user_id)
                .where(ProjectMember.project_id == project_id, ProfileSkill.kind == "has")
                .distinct()
            )
        )
        missing_by_id: dict[int, NamedItem] = {}
        for role in roles:
            if remaining[role.id] <= 0:
                continue
            for skill_id, slug, name in skills_by_role.get(role.id, []):
                if skill_id not in has_skill_ids:
                    missing_by_id[skill_id] = NamedItem(slug=slug, name=name)
        missing_skills = sorted(missing_by_id.values(), key=lambda item: item.name.casefold())

        visible_rows = [row for row in member_rows if not row.blocked]

        is_member = any(row.user_id == viewer_id for row in member_rows)
        conversation_id = None
        if is_member:
            conversation_id = await self.db.scalar(
                select(Conversation.id).where(Conversation.project_id == project_id)
            )

        viewer_app_row = (
            await self.db.execute(
                select(Application.id, Application.status, Application.role_id)
                .where(Application.project_id == project_id, Application.applicant_id == viewer_id)
                .order_by(Application.created_at.desc(), Application.id.desc())
                .limit(1)
            )
        ).first()
        viewer_application = (
            ViewerApplication(
                id=viewer_app_row.id,
                status=cast(ApplicationStatus, viewer_app_row.status),
                role_id=viewer_app_row.role_id,
            )
            if viewer_app_row is not None
            else None
        )

        return ProjectDetail(
            id=project.id,
            title=project.title,
            summary=project.summary,
            capacity=project.capacity,
            status=cast(ProjectStatus, project.status),
            member_count=member_count,
            owner=Owner(user_id=project.owner_id, display_name=owner_name),
            roles=role_out,
            members=[
                Member(
                    user_id=row.user_id,
                    display_name=row.display_name,
                    member_role=cast(Literal["owner", "member"], row.member_role),
                    role=RoleRef(id=row.role_id, title=role_titles[row.role_id])
                    if row.role_id is not None
                    else None,
                    joined_at=row.joined_at,
                )
                for row in visible_rows
            ],
            missing_skills=missing_skills,
            conversation_id=conversation_id,
            viewer_application=viewer_application,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )

    # --- edit ---------------------------------------------------------------------------------

    async def update(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, data: ProjectUpdate
    ) -> ProjectDetail:
        if data.status == "full":
            raise AppError(422, "invalid_status", INVALID_STATUS)

        project = await self._owner_project(owner_id, project_id, lock=True)
        if data.title is not None:
            project.title = data.title
        if data.summary is not None:
            project.summary = data.summary

        member_count = await self._member_count(project_id)
        new_capacity = data.capacity if data.capacity is not None else project.capacity
        if data.capacity is not None and data.capacity < member_count:
            await self.db.rollback()
            raise AppError(422, "capacity_too_low", CAPACITY_TOO_LOW)

        if data.roles is not None:
            await self._replace_roles(project_id, data.roles)

        if data.status == "closed":
            new_status: ProjectStatus = "closed"
        elif data.status == "open":
            if member_count >= new_capacity:
                await self.db.rollback()
                raise AppError(409, "capacity_reached", CAPACITY_REACHED)
            new_status = "open"
        elif project.status == "open" and member_count >= new_capacity:
            new_status = "full"
        elif project.status == "full" and member_count < new_capacity:
            new_status = "open"
        else:
            new_status = cast(ProjectStatus, project.status)

        project.capacity = new_capacity
        project.status = new_status
        await self.db.commit()
        return await self.detail(owner_id, project_id)

    async def _replace_roles(self, project_id: uuid.UUID, roles: list[RoleUpdate]) -> None:
        existing = {
            role.id: role
            for role in await self.db.scalars(
                select(ProjectRole).where(ProjectRole.project_id == project_id)
            )
        }
        incoming_ids = {item.id for item in roles if item.id is not None}
        unknown = incoming_ids - existing.keys()
        if unknown:
            await self.db.rollback()
            raise AppError(422, "unknown_role", UNKNOWN_ROLE)

        holder_counts: dict[uuid.UUID | None, int] = dict(
            (
                await self.db.execute(
                    select(ProjectMember.role_id, func.count())
                    .where(
                        ProjectMember.project_id == project_id, ProjectMember.role_id.is_not(None)
                    )
                    .group_by(ProjectMember.role_id)
                )
            ).all()
        )
        pending_role_ids = set(
            await self.db.scalars(
                select(Application.role_id).where(
                    Application.project_id == project_id,
                    Application.status == "pending",
                    Application.role_id.is_not(None),
                )
            )
        )

        to_remove = existing.keys() - incoming_ids
        for role_id in to_remove:
            if holder_counts.get(role_id, 0) > 0 or role_id in pending_role_ids:
                await self.db.rollback()
                raise AppError(409, "cannot_remove_role_in_use", CANNOT_REMOVE_ROLE_IN_USE)

        for item in roles:
            if item.id is not None and item.open_slots < holder_counts.get(item.id, 0):
                await self.db.rollback()
                raise AppError(422, "role_open_slots_too_low", ROLE_OPEN_SLOTS_TOO_LOW)

        try:
            skill_ids = await self._skill_ids({slug for item in roles for slug in item.skills})
        except AppError:
            await self.db.rollback()
            raise

        if to_remove:
            await self.db.execute(delete(ProjectRole).where(ProjectRole.id.in_(to_remove)))

        for item in roles:
            if item.id is not None:
                role = existing[item.id]
                role.title = item.title
                role.open_slots = item.open_slots
                role_id = role.id
                await self.db.execute(delete(ProjectSkill).where(ProjectSkill.role_id == role_id))
            else:
                role = ProjectRole(
                    project_id=project_id, title=item.title, open_slots=item.open_slots
                )
                self.db.add(role)
                await self.db.flush()
                role_id = role.id
            if item.skills:
                await self.db.execute(
                    insert(ProjectSkill),
                    [{"role_id": role_id, "skill_id": skill_ids[slug]} for slug in item.skills],
                )

    # --- apply and review -----------------------------------------------------------------------

    async def apply(
        self, applicant_id: uuid.UUID, project_id: uuid.UUID, role_id: uuid.UUID | None
    ) -> ApplicationOut:
        self._limit_apply(applicant_id)
        # FOR SHARE: the insert below is already serialised by its own FK/unique checks, but
        # this lock is what stops the early-exit reads just below (closed / full / role_full)
        # from deciding on a stale status or capacity snapshot while an accept or a PATCH (both
        # FOR UPDATE) is still in flight -- they wait for us to commit; we don't wait for each
        # other, since several FOR SHARE holders can coexist.
        project = await self.db.scalar(
            select(Project)
            .where(Project.id == project_id, ~blocked_between(Project.owner_id, applicant_id))
            .with_for_update(read=True)
        )
        if project is None:
            await self.db.rollback()
            raise AppError(404, "not_found", NOT_FOUND)
        if project.owner_id == applicant_id:
            await self.db.rollback()
            raise AppError(422, "cannot_apply_to_own_project", CANNOT_APPLY_TO_OWN_PROJECT)

        role: ProjectRole | None = None
        if role_id is not None:
            role = await self.db.scalar(
                select(ProjectRole).where(
                    ProjectRole.id == role_id, ProjectRole.project_id == project_id
                )
            )
            if role is None:
                await self.db.rollback()
                raise AppError(422, "unknown_role", UNKNOWN_ROLE)

        already_member = await self.db.scalar(
            select(ProjectMember.user_id).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == applicant_id
            )
        )
        if already_member is not None:
            await self.db.rollback()
            raise AppError(409, "already_a_member", ALREADY_A_MEMBER)
        if project.status == "closed":
            await self.db.rollback()
            raise AppError(409, "project_closed", PROJECT_CLOSED)
        if project.status == "full":
            await self.db.rollback()
            raise AppError(409, "project_full", PROJECT_FULL)
        if role is not None:
            holders = await self._role_holder_count(project_id, role.id)
            if holders >= role.open_slots:
                await self.db.rollback()
                raise AppError(409, "role_full", ROLE_FULL)

        await self.db.execute(
            pg_insert(Application)
            .values(project_id=project_id, applicant_id=applicant_id, role_id=role_id)
            .on_conflict_do_nothing(
                index_elements=[Application.project_id, Application.applicant_id],
                index_where=text(PENDING_SQL),
            )
        )
        # Read back inside this same transaction, before committing: a decision made by a
        # withdraw or a block (neither takes our FOR SHARE project lock) can still land between
        # our insert and this read, so fall back to the newest application rather than 500 if
        # the pending row is already gone by the time we look.
        application, name, role_title = await self._pending_or_latest(project_id, applicant_id)
        await self.db.commit()
        return self._application_out(application, name, role_title)

    async def applications(
        self,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        status: list[ApplicationStatus] | None,
        limit: int,
        offset: int,
    ) -> ApplicationPage:
        await self._owner_project(owner_id, project_id)
        query = self._application_select().where(
            Application.project_id == project_id,
            ~blocked_between(Application.applicant_id, owner_id),
        )
        if status is not None:
            query = query.where(Application.status.in_(status))
        rows = (
            await self.db.execute(
                query.order_by(Application.created_at.desc(), Application.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        items = [self._application_out(*row) for row in rows[:limit]]
        return ApplicationPage(items=items, next_offset=offset + limit if has_more else None)

    async def accept(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationOut:
        project = await self._owner_project(owner_id, project_id, lock=True)
        application = await self.db.scalar(
            select(Application)
            .where(Application.id == application_id, Application.project_id == project_id)
            .with_for_update()
        )
        if application is None:
            await self.db.rollback()
            raise AppError(404, "not_found", APPLICATION_NOT_FOUND)
        if application.status != "pending" or await self._blocked(
            project.owner_id, application.applicant_id
        ):
            await self.db.rollback()
            raise AppError(409, "application_not_pending", APPLICATION_NOT_PENDING)
        if project.status == "closed":
            await self.db.rollback()
            raise AppError(409, "project_closed", PROJECT_CLOSED)

        member_count = await self._member_count(project_id)
        if member_count >= project.capacity:
            await self.db.rollback()
            raise AppError(409, "capacity_reached", CAPACITY_REACHED)

        if application.role_id is not None:
            role = await self.db.get(ProjectRole, application.role_id)
            holders = await self._role_holder_count(project_id, application.role_id)
            if role is None or holders >= role.open_slots:
                await self.db.rollback()
                raise AppError(409, "role_full", ROLE_FULL)

        self.db.add(
            ProjectMember(
                project_id=project_id,
                user_id=application.applicant_id,
                role_id=application.role_id,
                member_role="member",
            )
        )
        await self.db.execute(
            update(Application)
            .where(Application.id == application_id)
            .values(status="accepted", decided_at=func.now())
        )
        conversation_id = await self.db.scalar(
            select(Conversation.id).where(Conversation.project_id == project_id)
        )
        await self.db.execute(
            pg_insert(ConversationMember)
            .values(conversation_id=conversation_id, user_id=application.applicant_id, active=True)
            .on_conflict_do_update(
                index_elements=[ConversationMember.conversation_id, ConversationMember.user_id],
                set_={"active": True},
            )
        )
        if member_count + 1 >= project.capacity:
            await self.db.execute(
                update(Project).where(Project.id == project_id).values(status="full")
            )
        await self.db.commit()
        return await self._application_view(application_id)

    async def reject(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationOut:
        await self._owner_project(owner_id, project_id)
        decided_id = await self.db.scalar(
            update(Application)
            .where(
                Application.id == application_id,
                Application.project_id == project_id,
                Application.status == "pending",
            )
            .values(status="rejected", decided_at=func.now())
            .returning(Application.id)
        )
        if decided_id is None:
            await self.db.rollback()
            await self._application_decided_or_missing(application_id, project_id)
        await self.db.commit()
        return await self._application_view(application_id)

    async def withdraw(
        self, applicant_id: uuid.UUID, project_id: uuid.UUID, application_id: uuid.UUID
    ) -> None:
        withdrawn_id = await self.db.scalar(
            update(Application)
            .where(
                Application.id == application_id,
                Application.project_id == project_id,
                Application.applicant_id == applicant_id,
                Application.status == "pending",
            )
            .values(status="withdrawn", decided_at=func.now())
            .returning(Application.id)
        )
        if withdrawn_id is None:
            await self.db.rollback()
            state = await self.db.scalar(
                select(Application.status).where(
                    Application.id == application_id,
                    Application.project_id == project_id,
                    Application.applicant_id == applicant_id,
                )
            )
            if state is None:
                raise AppError(404, "not_found", APPLICATION_NOT_FOUND)
            raise AppError(409, "application_not_pending", APPLICATION_NOT_PENDING)
        await self.db.commit()

    async def my_applications(
        self, viewer_id: uuid.UUID, limit: int, offset: int
    ) -> MyApplicationPage:
        query = (
            select(Application, Profile.display_name, ProjectRole.title, Project.id, Project.title)
            .join(Project, Project.id == Application.project_id)
            .outerjoin(Profile, Profile.user_id == Application.applicant_id)
            .outerjoin(ProjectRole, ProjectRole.id == Application.role_id)
            .where(
                Application.applicant_id == viewer_id, ~blocked_between(Project.owner_id, viewer_id)
            )
            .order_by(Application.created_at.desc(), Application.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self.db.execute(query)).all()
        has_more = len(rows) > limit
        items = [
            MyApplicationOut(
                **self._application_out(application, name, role_title).model_dump(),
                project=ProjectRef(id=project_id, title=project_title),
            )
            for application, name, role_title, project_id, project_title in rows[:limit]
        ]
        return MyApplicationPage(items=items, next_offset=offset + limit if has_more else None)

    # --- helpers ----------------------------------------------------------------------------

    async def _owner_project(
        self, user_id: uuid.UUID, project_id: uuid.UUID, *, lock: bool = False
    ) -> Project:
        query = select(Project).where(
            Project.id == project_id, ~blocked_between(Project.owner_id, user_id)
        )
        if lock:
            query = query.with_for_update()
        project = await self.db.scalar(query)
        if project is None:
            await self.db.rollback()
            raise AppError(404, "not_found", NOT_FOUND)
        if project.owner_id != user_id:
            await self.db.rollback()
            raise AppError(403, "forbidden", FORBIDDEN)
        return project

    async def _member_count(self, project_id: uuid.UUID) -> int:
        count = await self.db.scalar(
            select(func.count())
            .select_from(ProjectMember)
            .where(ProjectMember.project_id == project_id)
        )
        return int(count or 0)

    async def _role_holder_count(self, project_id: uuid.UUID, role_id: uuid.UUID) -> int:
        count = await self.db.scalar(
            select(func.count())
            .select_from(ProjectMember)
            .where(ProjectMember.project_id == project_id, ProjectMember.role_id == role_id)
        )
        return int(count or 0)

    async def _member_counts(self, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not project_ids:
            return {}
        rows = await self.db.execute(
            select(ProjectMember.project_id, func.count())
            .where(ProjectMember.project_id.in_(project_ids))
            .group_by(ProjectMember.project_id)
        )
        return dict(rows.all())

    async def _role_summaries(
        self, project_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[RoleSummary]]:
        if not project_ids:
            return {}
        rows = (
            await self.db.execute(
                select(
                    ProjectRole.id,
                    ProjectRole.project_id,
                    ProjectRole.title,
                    ProjectRole.open_slots,
                    # No extra FILTER needed: the outer join already restricts matches to this
                    # exact role, so a plain count over the joined rows is the holder count.
                    func.count(ProjectMember.user_id),
                )
                .outerjoin(ProjectMember, ProjectMember.role_id == ProjectRole.id)
                .where(ProjectRole.project_id.in_(project_ids))
                .group_by(ProjectRole.id)
                .order_by(ProjectRole.created_at, ProjectRole.id)
            )
        ).all()
        by_project: dict[uuid.UUID, list[RoleSummary]] = defaultdict(list)
        for role_id, project_id, title, open_slots, holders in rows:
            by_project[project_id].append(
                RoleSummary(
                    id=role_id,
                    title=title,
                    open_slots=open_slots,
                    remaining_slots=max(0, open_slots - holders),
                )
            )
        return by_project

    async def _blocked(self, a: uuid.UUID, b: uuid.UUID) -> bool:
        return bool(await self.db.scalar(select(blocked_between(a, b))))

    async def _skill_ids(self, slugs: set[str]) -> dict[str, int]:
        if not slugs:
            return {}
        rows = await self.db.execute(select(Skill.slug, Skill.id).where(Skill.slug.in_(slugs)))
        found = dict(rows.all())
        if len(found) != len(slugs):
            raise AppError(422, "unknown_skill", UNKNOWN_SKILL)
        return found

    def _application_select(self) -> Select[Application, str, str]:
        return (
            select(Application, Profile.display_name, ProjectRole.title)
            .outerjoin(Profile, Profile.user_id == Application.applicant_id)
            .outerjoin(ProjectRole, ProjectRole.id == Application.role_id)
        )

    def _application_out(
        self, application: Application, applicant_name: str | None, role_title: str | None
    ) -> ApplicationOut:
        return ApplicationOut(
            id=application.id,
            project_id=application.project_id,
            applicant=Applicant(user_id=application.applicant_id, display_name=applicant_name),
            role=RoleRef(id=application.role_id, title=role_title)
            if application.role_id is not None
            else None,
            status=cast(ApplicationStatus, application.status),
            created_at=application.created_at,
            decided_at=application.decided_at,
        )

    async def _application_view(self, application_id: uuid.UUID) -> ApplicationOut:
        application, name, role_title = (
            await self.db.execute(
                self._application_select().where(Application.id == application_id)
            )
        ).one()
        return self._application_out(application, name, role_title)

    async def _pending_or_latest(
        self, project_id: uuid.UUID, applicant_id: uuid.UUID
    ) -> tuple[Application, str | None, str | None]:
        """The pending application `apply()` just inserted (or that was already there via
        ON CONFLICT). If a decision landed on it before we could read it back, fall back to the
        applicant's newest application for this project instead of raising -- there must be at
        least one, since we either inserted it ourselves or conflicted with an existing row."""
        pending = (
            await self.db.execute(
                self._application_select().where(
                    Application.project_id == project_id,
                    Application.applicant_id == applicant_id,
                    Application.status == "pending",
                )
            )
        ).first()
        if pending is not None:
            return pending
        return (
            await self.db.execute(
                self._application_select()
                .where(
                    Application.project_id == project_id, Application.applicant_id == applicant_id
                )
                .order_by(Application.created_at.desc(), Application.id.desc())
                .limit(1)
            )
        ).one()

    async def _application_decided_or_missing(
        self, application_id: uuid.UUID, project_id: uuid.UUID
    ) -> None:
        state = await self.db.scalar(
            select(Application.status).where(
                Application.id == application_id, Application.project_id == project_id
            )
        )
        if state is None:
            raise AppError(404, "not_found", APPLICATION_NOT_FOUND)
        raise AppError(409, "application_not_pending", APPLICATION_NOT_PENDING)

    def _limit_create(self, owner_id: uuid.UUID) -> None:
        retry_after = self.limiter.hit("project-create", str(owner_id), CREATE_LIMIT)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many projects created. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )

    def _limit_apply(self, applicant_id: uuid.UUID) -> None:
        retry_after = self.limiter.hit("project-apply", str(applicant_id), APPLY_LIMIT)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                "Too many applications. Please wait and try again.",
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
