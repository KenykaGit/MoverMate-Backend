"""The read model of projects against real rows: visibility, ordering, hidden members."""

from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.common.ids import ProjectId, UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.projects.entity import Project
from app.domain.users.entity import User
from app.infrastructure.db.models import ProjectCollaboratorModel
from app.infrastructure.db.repositories.project import SqlAlchemyProjectRepository
from app.infrastructure.db.repositories.project_reader import SqlAlchemyProjectReader
from app.infrastructure.db.repositories.user import SqlAlchemyUserRepository
from tests.factories import make_project, make_user

type SessionMaker = async_sessionmaker[AsyncSession]


async def save(
    session_maker: SessionMaker, users: Sequence[User], projects: Sequence[Project] = ()
) -> None:
    async with session_maker() as session:
        for user in users:
            await SqlAlchemyUserRepository(session).add(user)
        for project in projects:
            await SqlAlchemyProjectRepository(session).add(project)
        await session.commit()


def reader(session: AsyncSession) -> SqlAlchemyProjectReader:
    return SqlAlchemyProjectReader(session)


def project_of(owner: User, moving_date: date, *members: User) -> Project:
    project = make_project(owner_id=owner.id, moving_date=moving_date)
    for member in members:
        project.add_member(member.id)
    return project


async def set_joined_at(
    session_maker: SessionMaker, project_id: ProjectId, user_id: UserId, at: datetime
) -> None:
    c = ProjectCollaboratorModel
    async with session_maker() as session:
        await session.execute(
            update(c).where(c.project_id == project_id, c.user_id == user_id).values(created_at=at)
        )
        await session.commit()


class TestListForMember:
    async def test_active_member_projects_by_moving_date_then_id(
        self, session_maker: SessionMaker
    ) -> None:
        viewer, other = make_user(username="viewer"), make_user(username="other")
        late = project_of(viewer, date(2027, 3, 1))
        early = project_of(viewer, date(2027, 1, 1))
        foreign = project_of(other, date(2027, 2, 1), viewer)
        same_day = project_of(viewer, date(2027, 1, 1))
        hidden = project_of(other, date(2027, 1, 15))
        deleted = project_of(viewer, date(2027, 1, 10))
        deleted.soft_delete(by=viewer.id)
        await save(
            session_maker, [viewer, other], [late, early, foreign, same_day, hidden, deleted]
        )

        async with session_maker() as session:
            listed = await reader(session).list_for_member(viewer.id)

        first_two = sorted([early.id, same_day.id])
        assert [p.id for p in listed] == [*first_two, foreign.id, late.id]
        assert [p.is_owner for p in listed] == [True, True, False, True]
        assert listed[2].owner.username == "other"

    async def test_nothing_for_a_user_without_projects(self, session_maker: SessionMaker) -> None:
        user = make_user()
        await save(session_maker, [user])

        async with session_maker() as session:
            assert await reader(session).list_for_member(user.id) == []


class TestGet:
    async def test_project_as_its_member_sees_it(self, session_maker: SessionMaker) -> None:
        owner, member = make_user(username="owner"), make_user(username="member")
        project = project_of(owner, date(2027, 1, 1), member)
        for days in (30, 0, 7):
            project.add_moving_reminder(MovingReminderOffset(days))
        await save(session_maker, [owner, member], [project])

        async with session_maker() as session:
            as_owner = await reader(session).get(project.id, viewer_id=owner.id)
            as_member = await reader(session).get(project.id, viewer_id=member.id)

        assert as_owner is not None
        assert as_member is not None
        assert as_owner.reminder_offsets_days == (0, 7, 30)
        assert (as_owner.name, as_owner.moving_date, as_owner.time_zone) == (
            "Moving to Kazan",
            date(2027, 1, 1),
            "Europe/Moscow",
        )
        assert (as_owner.owner.id, as_owner.owner.username) == (owner.id, "owner")
        assert (as_owner.is_owner, as_member.is_owner) == (True, False)
        assert as_owner.created_at.tzinfo is not None

    async def test_none_for_a_stranger_and_for_a_deleted_project(
        self, session_maker: SessionMaker
    ) -> None:
        owner, stranger = make_user(username="owner"), make_user(username="stranger")
        project = project_of(owner, date(2027, 1, 1))
        deleted = project_of(owner, date(2027, 1, 1))
        deleted.soft_delete(by=owner.id)
        await save(session_maker, [owner, stranger], [project, deleted])

        async with session_maker() as session:
            assert await reader(session).get(project.id, viewer_id=stranger.id) is None
            assert await reader(session).get(deleted.id, viewer_id=owner.id) is None


class TestCollaborators:
    async def test_owner_first_then_by_joining_time(self, session_maker: SessionMaker) -> None:
        owner = make_user(username="owner")
        first, second = make_user(username="first"), make_user(username="second")
        project = project_of(owner, date(2027, 1, 1), first, second)
        await save(session_maker, [owner, first, second], [project])
        # Even a member who (on paper) joined before the owner comes after them.
        await set_joined_at(session_maker, project.id, first.id, datetime(2020, 1, 1, tzinfo=UTC))
        await set_joined_at(session_maker, project.id, second.id, datetime(2020, 1, 2, tzinfo=UTC))

        async with session_maker() as session:
            listed = await reader(session).list_collaborators(project.id)

        assert [(c.user.username, c.is_owner) for c in listed] == [
            ("owner", True),
            ("first", False),
            ("second", False),
        ]
        assert listed[1].created_at == datetime(2020, 1, 1, tzinfo=UTC)

    async def test_members_with_a_deleted_account_are_hidden(
        self, session_maker: SessionMaker
    ) -> None:
        owner, gone = make_user(username="owner"), make_user(username="gone", is_active=False)
        project = project_of(owner, date(2027, 1, 1), gone)
        await save(session_maker, [owner, gone], [project])

        async with session_maker() as session:
            listed = await reader(session).list_collaborators(project.id)
            hidden = await reader(session).get_collaborator(project.id, gone.id)

        assert [c.user.id for c in listed] == [owner.id]
        assert hidden is None

    async def test_get_collaborator(self, session_maker: SessionMaker) -> None:
        owner, member = make_user(username="owner"), make_user(username="member")
        stranger = make_user(username="stranger")
        project = project_of(owner, date(2027, 1, 1), member)
        await save(session_maker, [owner, member, stranger], [project])

        async with session_maker() as session:
            found = await reader(session).get_collaborator(project.id, member.id)
            missing = await reader(session).get_collaborator(project.id, stranger.id)

        assert found is not None
        assert (found.user.username, found.is_owner) == ("member", False)
        assert missing is None
