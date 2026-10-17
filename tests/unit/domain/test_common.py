from datetime import timedelta

import pytest

from app.domain.common.errors import InvalidReminderOffsetError
from app.domain.common.reminders import (
    REMINDER_OFFSET_MAX_HOURS,
    REMINDER_OFFSET_MIN_HOURS,
    ReminderOffset,
)


class TestReminderOffset:
    @pytest.mark.parametrize("hours", [REMINDER_OFFSET_MIN_HOURS, 2, REMINDER_OFFSET_MAX_HOURS])
    def test_any_whole_hours_in_range(self, hours: int) -> None:
        assert ReminderOffset(hours).delta == timedelta(hours=hours)

    @pytest.mark.parametrize(
        "hours", [REMINDER_OFFSET_MIN_HOURS - 1, -5, REMINDER_OFFSET_MAX_HOURS + 1]
    )
    def test_rejects_out_of_range(self, hours: int) -> None:
        with pytest.raises(InvalidReminderOffsetError):
            ReminderOffset(hours)

    def test_equal_by_value(self) -> None:
        assert ReminderOffset(24) == ReminderOffset(24)
        assert len({ReminderOffset(24), ReminderOffset(24), ReminderOffset(2)}) == 2
