"""Called from `TrustService.block`, in the same transaction, before its commit."""

import uuid

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.chat.models import Conversation, ConversationMember
from app.modules.teams.models import Application, Project, ProjectMember


async def end_team_ties(db: AsyncSession, a: uuid.UUID, b: uuid.UUID) -> None:
    """Between the blocker and the target: withdraw pending applications first (an accept in
    flight holds the application lock, so this waits for it and the membership step below then
    sees the new member), then remove whichever of the two is an ordinary member of a project
    the other owns. A block between two ordinary members of the same team changes nothing."""
    pending_ids = (
        select(Application.id)
        .join(Project, Project.id == Application.project_id)
        .where(
            Application.status == "pending",
            or_(
                and_(Application.applicant_id == a, Project.owner_id == b),
                and_(Application.applicant_id == b, Project.owner_id == a),
            ),
        )
    )
    await db.execute(
        update(Application)
        .where(Application.id.in_(pending_ids))
        .values(status="withdrawn", decided_at=func.now())
    )

    removed = (
        await db.execute(
            delete(ProjectMember)
            .where(
                ProjectMember.member_role == "member",
                or_(
                    and_(
                        ProjectMember.user_id == a,
                        ProjectMember.project_id.in_(
                            select(Project.id).where(Project.owner_id == b)
                        ),
                    ),
                    and_(
                        ProjectMember.user_id == b,
                        ProjectMember.project_id.in_(
                            select(Project.id).where(Project.owner_id == a)
                        ),
                    ),
                ),
            )
            .returning(ProjectMember.project_id, ProjectMember.user_id)
        )
    ).all()
    if not removed:
        return

    project_ids = {project_id for project_id, _ in removed}
    conversations = dict(
        (
            await db.execute(
                select(Conversation.project_id, Conversation.id).where(
                    Conversation.project_id.in_(project_ids)
                )
            )
        ).all()
    )
    for project_id, user_id in removed:
        conversation_id = conversations.get(project_id)
        if conversation_id is not None:
            await db.execute(
                update(ConversationMember)
                .where(
                    ConversationMember.conversation_id == conversation_id,
                    ConversationMember.user_id == user_id,
                )
                .values(active=False)
            )
