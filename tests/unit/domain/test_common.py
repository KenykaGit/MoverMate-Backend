from datetime import timedelta

from app.domain.common.errors import (
    InvalidMovingReminderOffsetError,
    InvalidReminderOffsetError,
)
from app.domain.common.reminders import (
    MOVING_REMINDER_MAX_DAYS,
    MOVING_REMINDER_MIN_DAYS,
    REMINDER_OFFSET_MAX_HOURS,
    REMINDER_OFFSET_MIN_HOURS,
    MovingReminderOffset,
    ReminderOffset,
)
from tests.checks import not_rejected


def test_reminder_offset_takes_any_whole_hours_in_range() -> None:
    hours = [REMINDER_OFFSET_MIN_HOURS, 2, REMINDER_OFFSET_MAX_HOURS]
    outside = [REMINDER_OFFSET_MIN_HOURS - 1, -5, REMINDER_OFFSET_MAX_HOURS + 1]

    assert [ReminderOffset(h).delta for h in hours] == [timedelta(hours=h) for h in hours]
    assert not_rejected((ReminderOffset, h, InvalidReminderOffsetError) for h in outside) == []


def test_reminder_offset_is_equal_by_value() -> None:
    assert ReminderOffset(24) == ReminderOffset(24)
    assert len({ReminderOffset(24), ReminderOffset(24), ReminderOffset(2)}) == 2


def test_moving_reminder_offset_takes_any_whole_days_in_range() -> None:
    days = [MOVING_REMINDER_MIN_DAYS, 7, MOVING_REMINDER_MAX_DAYS]
    outside = [MOVING_REMINDER_MIN_DAYS - 1, MOVING_REMINDER_MAX_DAYS + 1]

    assert [MovingReminderOffset(d).days for d in days] == days
    assert (
        not_rejected((MovingReminderOffset, d, InvalidMovingReminderOffsetError) for d in outside)
        == []
    )
