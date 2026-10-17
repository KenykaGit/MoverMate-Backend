from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Self

from app.domain.common.entity import Entity
from app.domain.common.ids import ProjectId, UserId, new_id
from app.domain.common.reminders import MovingReminderOffset
from app.domain.projects.errors import CannotRemoveOwnerError, NotProjectOwnerError
from app.domain.projects.value_objects import (
    Address,
    ProjectName,
    QrAccess,
    QrCodeStrategy,
    TimeZone,
)

MOVING_REMINDER_LOCAL_TIME = time(9)
"""Moving date reminders arrive at this time of day, in the time zone of the project."""


@dataclass(frozen=True, slots=True)
class MovingRemindersChange:
    added: frozenset[MovingReminderOffset]
    removed: frozenset[MovingReminderOffset]

    def __bool__(self) -> bool:
        return bool(self.added or self.removed)


@dataclass(eq=False, kw_only=True, slots=True)
class Project(Entity[ProjectId]):
    """A move. Its members are equal collaborators; the owner is one of them.

    Members belong to the aggregate: there are few of them, and the rule that
    the owner never leaves needs the whole set.
    """

    owner_id: UserId
    name: ProjectName
    moving_date: date
    origin_address: Address | None
    destination_address: Address | None
    qr_code_strategy: QrCodeStrategy
    time_zone: TimeZone
    """Where the move takes place; turns the moving date into moments of reminders."""
    member_ids: set[UserId]
    """Always contains owner_id."""
    moving_reminders: set[MovingReminderOffset]
    """How many days before the moving date every member is reminded."""
    is_active: bool
    """Soft-delete flag: False hides the project with everything in it."""

    @classmethod
    def create(
        cls,
        *,
        owner_id: UserId,
        name: ProjectName,
        moving_date: date,
        time_zone: TimeZone,
        origin_address: Address | None = None,
        destination_address: Address | None = None,
        qr_code_strategy: QrCodeStrategy = QrCodeStrategy.PROTECTED,
    ) -> Self:
        """A new active project; its owner is its first member."""
        return cls(
            id=ProjectId(new_id()),
            owner_id=owner_id,
            name=name,
            moving_date=moving_date,
            origin_address=origin_address,
            destination_address=destination_address,
            qr_code_strategy=qr_code_strategy,
            time_zone=time_zone,
            member_ids={owner_id},
            moving_reminders=set(),
            is_active=True,
        )

    def is_member(self, user_id: UserId) -> bool:
        return user_id in self.member_ids

    def add_member(self, user_id: UserId) -> None:
        """Idempotent: adding a member twice changes nothing."""
        self.member_ids.add(user_id)

    def remove_member(self, user_id: UserId) -> None:
        """Idempotent for non-members. The owner cannot be removed."""
        if user_id == self.owner_id:
            raise CannotRemoveOwnerError("The owner cannot be removed from the project")
        self.member_ids.discard(user_id)

    def qr_access(self, viewer_id: UserId | None) -> QrAccess:
        """What `viewer_id` (None: not logged in) may do with a box card opened by QR."""
        if viewer_id is not None and self.is_member(viewer_id):
            return QrAccess.WRITE
        if self.qr_code_strategy is QrCodeStrategy.OPEN:
            return QrAccess.READ
        # PROTECTED: a stranger must not even learn that the box exists.
        return QrAccess.LOGIN_REQUIRED if viewer_id is None else QrAccess.NOT_FOUND

    def rename(self, name: ProjectName) -> None:
        self.name = name

    def reschedule(self, moving_date: date) -> None:
        self.moving_date = moving_date

    def change_addresses(self, *, origin: Address | None, destination: Address | None) -> None:
        self.origin_address = origin
        self.destination_address = destination

    def change_time_zone(self, time_zone: TimeZone) -> None:
        self.time_zone = time_zone

    def change_qr_code_strategy(self, strategy: QrCodeStrategy) -> None:
        """Applies to labels already printed: access is decided when a code is opened."""
        self.qr_code_strategy = strategy

    def add_moving_reminder(self, offset: MovingReminderOffset) -> bool:
        """False if the project already had this reminder."""
        if offset in self.moving_reminders:
            return False
        self.moving_reminders.add(offset)
        return True

    def remove_moving_reminder(self, offset: MovingReminderOffset) -> bool:
        """False if the project had no such reminder."""
        if offset not in self.moving_reminders:
            return False
        self.moving_reminders.remove(offset)
        return True

    def replace_moving_reminders(
        self, offsets: Iterable[MovingReminderOffset]
    ) -> MovingRemindersChange:
        """Keep exactly `offsets` (duplicates collapse); report what was added and removed."""
        wanted = frozenset(offsets)
        change = MovingRemindersChange(
            added=wanted - self.moving_reminders, removed=frozenset(self.moving_reminders - wanted)
        )
        self.moving_reminders = set(wanted)
        return change

    def moving_reminder_at(self, offset: MovingReminderOffset) -> datetime:
        """When the reminder `offset` days before the move is due, in UTC.

        At MOVING_REMINDER_LOCAL_TIME of that day where the move takes place: a
        reminder at 03:00 because the server runs in UTC would be useless.
        """
        day = self.moving_date - timedelta(days=offset.days)
        local = datetime.combine(day, MOVING_REMINDER_LOCAL_TIME, tzinfo=self.time_zone.zone)
        return local.astimezone(UTC)

    def soft_delete(self, *, by: UserId) -> None:
        """Idempotent. Only the owner deletes a project; other members may only leave it."""
        if by != self.owner_id:
            raise NotProjectOwnerError("Only the owner can delete the project")
        self.is_active = False
