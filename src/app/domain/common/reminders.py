"""Reminder offsets: chosen on a task or a project, carried by the notifications
scheduled from them (shared kernel)."""

from dataclasses import dataclass
from datetime import timedelta

from app.domain.common.errors import InvalidMovingReminderOffsetError, InvalidReminderOffsetError

REMINDER_OFFSET_MIN_HOURS = 1
REMINDER_OFFSET_MAX_HOURS = 24 * 365
MOVING_REMINDER_MIN_DAYS = 1
MOVING_REMINDER_MAX_DAYS = 365


@dataclass(frozen=True, slots=True, order=True)
class ReminderOffset:
    """How many whole hours before a task's due time its assignees are reminded.

    Any number of hours within the range is allowed: presets such as "a day
    before" are a client concern.
    """

    hours: int

    def __post_init__(self) -> None:
        if not REMINDER_OFFSET_MIN_HOURS <= self.hours <= REMINDER_OFFSET_MAX_HOURS:
            raise InvalidReminderOffsetError(
                f"Reminder offset must be between {REMINDER_OFFSET_MIN_HOURS} "
                f"and {REMINDER_OFFSET_MAX_HOURS} hours"
            )

    @property
    def delta(self) -> timedelta:
        return timedelta(hours=self.hours)


@dataclass(frozen=True, slots=True, order=True)
class MovingReminderOffset:
    """How many whole days before the moving date the members of a project are reminded.

    The moving date has no time of day: the moment of sending is derived from it
    by the application.
    """

    days: int

    def __post_init__(self) -> None:
        if not MOVING_REMINDER_MIN_DAYS <= self.days <= MOVING_REMINDER_MAX_DAYS:
            raise InvalidMovingReminderOffsetError(
                f"Moving date reminder must be between {MOVING_REMINDER_MIN_DAYS} "
                f"and {MOVING_REMINDER_MAX_DAYS} days before the move"
            )
