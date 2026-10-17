from datetime import datetime, timedelta

import pytest

from app.domain.common.ids import ProjectId, UserId, new_id
from app.domain.common.reminders import ReminderOffset
from app.domain.tasks.entity import Task
from app.domain.tasks.errors import (
    InvalidDueDateError,
    InvalidTaskDescriptionError,
    InvalidTaskTitleError,
    ReminderWithoutDueDateError,
)
from app.domain.tasks.value_objects import (
    TASK_DESCRIPTION_MAX_LENGTH,
    TASK_TITLE_MAX_LENGTH,
    TaskDescription,
    TaskTemplate,
    TaskTitle,
)
from tests.factories import NOW, make_task

DUE = NOW + timedelta(days=10)
DAY = ReminderOffset(24)


class TestValueObjects:
    @pytest.mark.parametrize("value", [" ", "a" * (TASK_TITLE_MAX_LENGTH + 1)])
    def test_title_length(self, value: str) -> None:
        with pytest.raises(InvalidTaskTitleError):
            TaskTitle(value)

    def test_description_length(self) -> None:
        with pytest.raises(InvalidTaskDescriptionError):
            TaskDescription("a" * (TASK_DESCRIPTION_MAX_LENGTH + 1))


def test_naive_due_time_is_rejected() -> None:
    naive = datetime(2026, 8, 1, 12)  # noqa: DTZ001  # the point of the test

    with pytest.raises(InvalidDueDateError):
        make_task(due_at=naive)
    with pytest.raises(InvalidDueDateError):
        make_task().reschedule(naive)


def test_from_template_copies_title_and_description_only() -> None:
    template = TaskTemplate(title=TaskTitle("Pack documents"), description=TaskDescription("All"))
    project_id = ProjectId(new_id())

    task = Task.from_template(template, project_id=project_id)

    assert (task.project_id, task.title, task.description) == (
        project_id,
        template.title,
        template.description,
    )
    assert task.due_at is None
    assert not task.assignee_ids
    assert not task.is_done


class TestCompletion:
    def test_complete_keeps_the_first_time(self) -> None:
        task = make_task()

        assert task.complete(now=NOW)
        assert not task.complete(now=NOW + timedelta(hours=1))
        assert task.done_at == NOW
        assert task.is_done

    def test_reopen(self) -> None:
        task = make_task()
        task.complete(now=NOW)

        assert task.reopen()
        assert not task.reopen()
        assert not task.is_done
        assert task.done_at is None


class TestReminders:
    def test_need_a_due_time(self) -> None:
        task = make_task()

        with pytest.raises(ReminderWithoutDueDateError):
            task.add_reminder(DAY)

    def test_are_due_offset_before_the_task(self) -> None:
        task = make_task(due_at=DUE)

        assert task.add_reminder(DAY)
        assert not task.add_reminder(DAY)
        assert task.add_reminder(ReminderOffset(2))

        assert task.reminder_times() == {
            DAY: DUE - timedelta(days=1),
            ReminderOffset(2): DUE - timedelta(hours=2),
        }

    def test_move_with_the_due_time(self) -> None:
        task = make_task(due_at=DUE)
        task.add_reminder(DAY)

        task.reschedule(DUE + timedelta(days=1))

        assert task.reminder_times() == {DAY: DUE}

    def test_clearing_the_due_time_drops_them(self) -> None:
        task = make_task(due_at=DUE)
        task.add_reminder(DAY)

        task.reschedule(None)

        assert task.reminders == set()
        assert task.reminder_times() == {}

    def test_remove(self) -> None:
        task = make_task(due_at=DUE)
        task.add_reminder(DAY)

        assert task.remove_reminder(DAY)
        assert not task.remove_reminder(DAY)


def test_assign_and_unassign_report_changes() -> None:
    task = make_task()
    user = UserId(new_id())

    assert task.assign(user)
    assert not task.assign(user)
    assert task.assignee_ids == {user}
    assert task.unassign(user)
    assert not task.unassign(user)
