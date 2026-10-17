from datetime import UTC, date, datetime, timedelta

import pytest

from app.application.projects.services import MovingReminderScheduler
from app.domain.projects.entity import Project
from app.domain.users.entity import User
from tests.factories import make_project, make_user
from tests.fakes import (
    InMemoryNotificationRepository,
    InMemoryProjectRepository,
    InMemoryUserRepository,
)


def in_days(days: int) -> date:
    """A date relative to today: reminders are queued against the real clock."""
    return datetime.now(UTC).date() + timedelta(days=days)


@pytest.fixture
def scheduler(notifications: InMemoryNotificationRepository) -> MovingReminderScheduler:
    return MovingReminderScheduler(notifications)


@pytest.fixture
async def owner(users: InMemoryUserRepository) -> User:
    user = make_user(username="owner")
    await users.add(user)
    return user


@pytest.fixture
async def member(users: InMemoryUserRepository) -> User:
    user = make_user(username="member")
    await users.add(user)
    return user


@pytest.fixture
async def stranger(users: InMemoryUserRepository) -> User:
    user = make_user(username="stranger")
    await users.add(user)
    return user


@pytest.fixture
async def project(projects: InMemoryProjectRepository, owner: User, member: User) -> Project:
    """A project of `owner` with `member`, moving in 30 days."""
    project = make_project(owner_id=owner.id, moving_date=in_days(30))
    project.add_member(member.id)
    await projects.add(project)
    return project
