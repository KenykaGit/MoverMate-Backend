"""Builders of domain objects with sensible defaults for tests."""

from datetime import UTC, date, datetime, timedelta

from app.domain.common.ids import ProjectId, UserId, new_id
from app.domain.inventory.box import Box
from app.domain.inventory.item import Item
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import BoxNumber, ItemName, RoomKind, RoomName
from app.domain.projects.entity import Project
from app.domain.projects.value_objects import ProjectName
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskTitle
from app.domain.tokens.entity import RefreshToken, TokenFamilyId, new_token_family
from app.domain.users.entity import User
from app.domain.users.value_objects import PasswordHash, Username

NOW = datetime(2026, 7, 1, 12, tzinfo=UTC)

PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"


def make_user(
    *,
    username: str = "john_doe",
    password_hash: str = PASSWORD_HASH,
    is_active: bool = True,
) -> User:
    user = User.register(username=Username(username), password_hash=PasswordHash(password_hash))
    if not is_active:
        user.soft_delete()
    return user


def user_state(user: User) -> tuple[object, ...]:
    """All attributes of a user (User equality compares ids only)."""
    return (user.id, user.username, user.password_hash, user.is_active)


def make_refresh_token(
    user_id: UserId,
    *,
    family_id: TokenFamilyId | None = None,
    token_hash: str | None = None,
    expires_in: timedelta = timedelta(days=1),
    revoked: bool = False,
) -> RefreshToken:
    """A token of `family_id`, or the first token of a new session."""
    now = datetime.now(UTC)
    token = RefreshToken.issue(
        user_id=user_id,
        family_id=family_id or new_token_family(),
        token_hash=token_hash or new_id().hex + new_id().hex,  # 64 hex chars
        expires_at=now + expires_in,
    )
    if revoked:
        token.revoke(now)
    return token


def make_project(*, owner_id: UserId | None = None) -> Project:
    return Project.create(
        owner_id=owner_id or UserId(new_id()),
        name=ProjectName("Moving to Kazan"),
        moving_date=date(2026, 8, 1),
    )


def make_room(
    *, project_id: ProjectId | None = None, kind: RoomKind = RoomKind.SOURCE, name: str = "Kitchen"
) -> Room:
    return Room.create(project_id=project_id or ProjectId(new_id()), kind=kind, name=RoomName(name))


def make_box(*, project_id: ProjectId | None = None, number: int = 1, now: datetime = NOW) -> Box:
    project_id = project_id or ProjectId(new_id())
    return Box.create(
        project_id=project_id,
        number=BoxNumber(number),
        destination=make_room(project_id=project_id, kind=RoomKind.DESTINATION),
        created_by=UserId(new_id()),
        now=now,
    )


def make_item(*, project_id: ProjectId | None = None, name: str = "Kettle") -> Item:
    project_id = project_id or ProjectId(new_id())
    return Item.create(
        project_id=project_id, source=make_room(project_id=project_id), name=ItemName(name)
    )


def make_task(*, due_at: datetime | None = None) -> Task:
    return Task.create(
        project_id=ProjectId(new_id()), title=TaskTitle("Order movers"), due_at=due_at
    )
