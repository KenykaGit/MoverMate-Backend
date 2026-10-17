"""Reminder offsets: chosen on a task, carried by its scheduled notifications (shared kernel)."""

from dataclasses import dataclass
from datetime import timedelta

from app.domain.common.errors import InvalidReminderOffsetError

REMINDER_OFFSET_MIN_HOURS = 1
REMINDER_OFFSET_MAX_HOURS = 24 * 365


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
