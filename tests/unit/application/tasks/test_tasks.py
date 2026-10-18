from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest

from app.application.exceptions import (
    AssigneeNotCollaboratorError,
    ProjectNotFoundError,
    TaskNotFoundError,
    UnknownTaskTemplateError,
)
from app.application.tasks.complete_task import CompleteTask
from app.application.tasks.create_task import CreateTask
from app.application.tasks.create_tasks_from_templates import CreateTasksFromTemplates
from app.application.tasks.delete_task import DeleteTask
from app.application.tasks.dto import (
    CreateTaskDTO,
    CreateTasksFromTemplatesDTO,
    ListTasksDTO,
    SetTaskAssigneesDTO,
    SetTaskRemindersDTO,
    TaskActionDTO,
    UpdateTaskDTO,
)
from app.application.tasks.get_task import GetTask
from app.application.tasks.list_task_templates import ListTaskTemplates
from app.application.tasks.list_tasks import ListTasks
from app.application.tasks.reopen_task import ReopenTask
from app.application.tasks.set_task_assignees import SetTaskAssignees
from app.application.tasks.set_task_reminders import SetTaskReminders
from app.application.tasks.update_task import UpdateTask
from app.domain.audit.entity import AuditAction
from app.domain.common.errors import InvalidReminderOffsetError
from app.domain.common.ids import new_id
from app.domain.notifications.notification import TaskReminder
from app.domain.tasks.errors import (
    InvalidDueDateError,
    InvalidTaskTitleError,
    ReminderWithoutDueDateError,
)
from app.domain.tasks.value_objects import TaskPhase
from tests.conftest import LogEvents
from tests.unit.application.tasks.conftest import TEMPLATES, Preset, in_hours, new_preset

ASSIGNED, REMINDER = "TASK_ASSIGNED", "TASK_REMINDER"


def action(preset: Preset, task_id: UUID, *, by: UUID | None = None) -> TaskActionDTO:
    return TaskActionDTO(user_id=by or preset.owner.id, task_id=task_id)


class TestCreate:
    async def test_with_assignees_and_reminders(self, preset: Preset) -> None:
        due = in_hours(72)

        task = await preset.task(
            description="Two companies",
            phase=TaskPhase.DURING,
            due_at=due,
            assignee_ids=(preset.owner.id, preset.member.id),
            reminder_offsets_hours=(24, 2, 24),
        )

        assert (task.title, task.description, task.phase, task.due_at, task.is_done) == (
            "Order movers",
            "Two companies",
            TaskPhase.DURING,
            due,
            False,
        )
        assert [user.username for user in task.assignees] == ["member", "owner"]
        assert task.reminder_offsets_hours == (2, 24)
        # The creator assigned themselves: only the member is told.
        assert preset.queued() == [
            ("member", ASSIGNED, None),
            ("member", REMINDER, 2),
            ("member", REMINDER, 24),
            ("owner", REMINDER, 2),
            ("owner", REMINDER, 24),
        ]
        assert preset.audit_log.actions() == [AuditAction.TASK_CREATED]
        assert preset.uow.commits == 1

    async def test_defaults(self, preset: Preset) -> None:
        task = await preset.task()

        assert (task.phase, task.due_at, task.assignees, task.reminder_offsets_hours) == (
            TaskPhase.BEFORE,
            None,
            (),
            (),
        )
        assert preset.queued() == []

    async def test_reminders_already_due_are_kept_but_not_queued(self, preset: Preset) -> None:
        task = await preset.task(
            due_at=in_hours(1), assignee_ids=(preset.member.id,), reminder_offsets_hours=(2,)
        )

        assert task.reminder_offsets_hours == (2,)
        assert preset.queued() == [("member", ASSIGNED, None)]

    async def test_rejections_write_nothing(self) -> None:
        cases: list[tuple[Callable[[Preset], dict[str, Any]], type[Exception]]] = [
            (lambda p: {"user_id": p.stranger.id}, ProjectNotFoundError),
            (lambda p: {"assignee_ids": (p.stranger.id,)}, AssigneeNotCollaboratorError),
            (lambda _: {"reminder_offsets_hours": (24,)}, ReminderWithoutDueDateError),
            (
                lambda _: {"due_at": in_hours(5), "reminder_offsets_hours": (0,)},
                InvalidReminderOffsetError,
            ),
            (lambda _: {"due_at": in_hours(5).replace(tzinfo=None)}, InvalidDueDateError),
            (lambda _: {"title": "  "}, InvalidTaskTitleError),
        ]
        for overrides, error in cases:
            preset = new_preset()
            values: dict[str, Any] = {
                "user_id": preset.owner.id,
                "project_id": preset.project.id,
                "title": "T",
            }
            data = CreateTaskDTO(**(values | overrides(preset)))

            with pytest.raises(error):
                await preset.make(CreateTask)(data)

            assert (preset.tasks.tasks, preset.queued(), preset.uow.commits) == ({}, [], 0)


class TestTemplates:
    async def test_list_by_phase(self, preset: Preset) -> None:
        templates = await preset.make(ListTaskTemplates)()

        assert [(t.id, t.phase, t.description) for t in templates] == [
            ("book-movers", TaskPhase.BEFORE, "Compare offers"),
            ("photograph-meters", TaskPhase.DURING, None),
            ("check-boxes", TaskPhase.AFTER, None),
        ]

    async def test_copies_the_chosen_ones_in_order_once(self, preset: Preset) -> None:
        create = preset.make(CreateTasksFromTemplates)

        tasks = await create(
            CreateTasksFromTemplatesDTO(
                user_id=preset.member.id,
                project_id=preset.project.id,
                template_ids=("check-boxes", "book-movers", "check-boxes"),
            )
        )

        assert [(t.title, t.phase, t.description) for t in tasks] == [
            ("Check boxes", TaskPhase.AFTER, None),
            ("Book movers", TaskPhase.BEFORE, "Compare offers"),
        ]
        assert [entry.details for entry in preset.audit_log.entries] == [
            {"template_id": "check-boxes"},
            {"template_id": "book-movers"},
        ]
        # No link to the template: choosing it again adds another task.
        await create(
            CreateTasksFromTemplatesDTO(
                user_id=preset.owner.id, project_id=preset.project.id, template_ids=("book-movers",)
            )
        )
        assert len(preset.tasks.tasks) == len(TEMPLATES)

    async def test_rejections_write_nothing(self, preset: Preset) -> None:
        create = preset.make(CreateTasksFromTemplates)
        cases = [
            (preset.owner.id, ("book-movers", "no-such-template"), UnknownTaskTemplateError),
            (preset.stranger.id, ("book-movers",), ProjectNotFoundError),
        ]
        for user_id, template_ids, error in cases:
            with pytest.raises(error):
                await create(
                    CreateTasksFromTemplatesDTO(
                        user_id=user_id, project_id=preset.project.id, template_ids=template_ids
                    )
                )

        assert (preset.tasks.tasks, preset.uow.commits) == ({}, 0)


class TestRead:
    async def test_list_in_plan_order_with_filters(self, preset: Preset) -> None:
        after = await preset.task(title="After", phase=TaskPhase.AFTER)
        undated = await preset.task(title="Undated")
        later = await preset.task(title="Later", due_at=in_hours(48))
        sooner = await preset.task(title="Sooner", due_at=in_hours(24))
        await preset.make(CompleteTask)(action(preset, sooner.id))
        deleted = await preset.task(title="Deleted")
        await preset.make(DeleteTask)(action(preset, deleted.id))
        list_tasks = preset.make(ListTasks)

        async def titles(**filters: Any) -> list[str]:
            data = ListTasksDTO(user_id=preset.member.id, project_id=preset.project.id, **filters)
            return [task.title for task in await list_tasks(data)]

        assert await titles() == ["Sooner", "Later", "Undated", "After"]
        assert await titles(phase=TaskPhase.AFTER) == [after.title]
        assert await titles(is_done=True) == ["Sooner"]
        assert await titles(is_done=False) == [later.title, undated.title, after.title]

    async def test_strangers_get_not_found(self, preset: Preset) -> None:
        task = await preset.task()

        with pytest.raises(TaskNotFoundError):
            await preset.make(GetTask)(action(preset, task.id, by=preset.stranger.id))
        with pytest.raises(TaskNotFoundError):
            await preset.make(GetTask)(action(preset, new_id()))
        with pytest.raises(ProjectNotFoundError):
            await preset.make(ListTasks)(
                ListTasksDTO(user_id=preset.stranger.id, project_id=preset.project.id)
            )


class TestUpdate:
    async def test_changes_only_what_was_sent(self, preset: Preset) -> None:
        task = await preset.task(description="Keep me")

        updated = await preset.make(UpdateTask)(
            UpdateTaskDTO(
                user_id=preset.member.id, task_id=task.id, title="Renamed", phase=TaskPhase.AFTER
            )
        )

        assert (updated.title, updated.phase, updated.description) == (
            "Renamed",
            TaskPhase.AFTER,
            "Keep me",
        )

    async def test_logs_the_sent_fields(self, preset: Preset, log_events: LogEvents) -> None:
        task = await preset.task()

        await preset.make(UpdateTask)(
            UpdateTaskDTO(user_id=preset.member.id, task_id=task.id, title="Renamed")
        )

        [event] = [e for e in log_events() if e["event"] == "task_updated"]
        assert (event["task_title"], event["fields"], event["rescheduled"]) == (
            "Renamed",
            ["title"],
            False,
        )
        assert event["user_id"] == str(preset.member.id)

    async def test_new_due_time_moves_pending_reminders(self, preset: Preset) -> None:
        task = await preset.task(
            due_at=in_hours(48), assignee_ids=(preset.member.id,), reminder_offsets_hours=(24,)
        )
        new_due = in_hours(96)

        await preset.make(UpdateTask)(
            UpdateTaskDTO(user_id=preset.owner.id, task_id=task.id, due_at=new_due)
        )

        [reminder] = [n for n in preset.notifications.pending() if n.type.value == REMINDER]
        assert reminder.scheduled_at == new_due - timedelta(hours=24)
        cancelled = [n for n in preset.notifications.notifications if not n.is_pending]
        assert [n.type.value for n in cancelled] == [REMINDER]

    async def test_clearing_the_due_time_drops_the_reminders(self, preset: Preset) -> None:
        task = await preset.task(
            due_at=in_hours(48), assignee_ids=(preset.member.id,), reminder_offsets_hours=(24,)
        )

        updated = await preset.make(UpdateTask)(
            UpdateTaskDTO(user_id=preset.owner.id, task_id=task.id, due_at=None)
        )

        assert (updated.due_at, updated.reminder_offsets_hours) == (None, ())
        assert preset.queued() == [("member", ASSIGNED, None)]


class TestCompletion:
    async def test_complete_cancels_notifications_and_is_idempotent(self, preset: Preset) -> None:
        task = await preset.task(
            due_at=in_hours(48), assignee_ids=(preset.member.id,), reminder_offsets_hours=(24,)
        )
        complete = preset.make(CompleteTask)

        first = await complete(action(preset, task.id))
        again = await complete(action(preset, task.id, by=preset.member.id))

        assert first.is_done
        assert again.done_at == first.done_at
        assert preset.queued() == []
        assert preset.audit_log.actions() == [AuditAction.TASK_CREATED, AuditAction.TASK_COMPLETED]

    async def test_reopen_queues_the_reminders_again_and_is_idempotent(
        self, preset: Preset
    ) -> None:
        task = await preset.task(
            due_at=in_hours(48), assignee_ids=(preset.member.id,), reminder_offsets_hours=(24,)
        )
        await preset.make(CompleteTask)(action(preset, task.id))
        reopen = preset.make(ReopenTask)

        reopened = await reopen(action(preset, task.id))
        again = await reopen(action(preset, task.id))

        assert (reopened.is_done, reopened.done_at, again.is_done) == (False, None, False)
        assert preset.queued() == [("member", REMINDER, 24)]
        assert preset.audit_log.actions()[-1] is AuditAction.TASK_REOPENED
        assert len(preset.audit_log.entries) == 3


class TestAssignees:
    async def test_replace_the_list(self, preset: Preset) -> None:
        task = await preset.task(
            due_at=in_hours(48), assignee_ids=(preset.owner.id,), reminder_offsets_hours=(24,)
        )
        set_assignees = preset.make(SetTaskAssignees)
        data = SetTaskAssigneesDTO(
            user_id=preset.owner.id, task_id=task.id, assignee_ids=(preset.member.id,)
        )

        updated = await set_assignees(data)
        before = list(preset.notifications.notifications)
        await set_assignees(data)  # the same set: nothing is written

        assert [user.username for user in updated.assignees] == ["member"]
        assert preset.queued() == [("member", ASSIGNED, None), ("member", REMINDER, 24)]
        assert preset.notifications.notifications == before

    async def test_self_assignment_is_not_announced_and_strangers_are_rejected(
        self, preset: Preset
    ) -> None:
        task = await preset.task()
        set_assignees = preset.make(SetTaskAssignees)

        await set_assignees(
            SetTaskAssigneesDTO(
                user_id=preset.member.id, task_id=task.id, assignee_ids=(preset.member.id,)
            )
        )
        with pytest.raises(AssigneeNotCollaboratorError):
            await set_assignees(
                SetTaskAssigneesDTO(
                    user_id=preset.owner.id, task_id=task.id, assignee_ids=(preset.stranger.id,)
                )
            )

        assert preset.queued() == []
        [stored] = preset.tasks.tasks.values()
        assert stored.assignee_ids == {preset.member.id}


class TestReminders:
    async def test_replace_the_list(self, preset: Preset) -> None:
        task = await preset.task(
            due_at=in_hours(48), assignee_ids=(preset.member.id,), reminder_offsets_hours=(24, 2)
        )

        updated = await preset.make(SetTaskReminders)(
            SetTaskRemindersDTO(user_id=preset.owner.id, task_id=task.id, offsets_hours=(2, 6))
        )

        assert updated.reminder_offsets_hours == (2, 6)
        assert preset.queued() == [
            ("member", ASSIGNED, None),
            ("member", REMINDER, 2),
            ("member", REMINDER, 6),
        ]
        cancelled = [n.target for n in preset.notifications.notifications if not n.is_pending]
        assert [t.offset.hours for t in cancelled if isinstance(t, TaskReminder)] == [24]

    async def test_need_a_due_time_unless_removed_all(self, preset: Preset) -> None:
        task = await preset.task()
        set_reminders = preset.make(SetTaskReminders)

        with pytest.raises(ReminderWithoutDueDateError):
            await set_reminders(
                SetTaskRemindersDTO(user_id=preset.owner.id, task_id=task.id, offsets_hours=(1,))
            )
        cleared = await set_reminders(
            SetTaskRemindersDTO(user_id=preset.owner.id, task_id=task.id, offsets_hours=())
        )

        assert cleared.reminder_offsets_hours == ()


async def test_delete_cancels_notifications_and_hides_the_task(preset: Preset) -> None:
    task = await preset.task(assignee_ids=(preset.member.id,))
    delete = preset.make(DeleteTask)

    await delete(action(preset, task.id, by=preset.member.id))

    assert preset.queued() == []
    operations: list[Callable[[], Awaitable[object]]] = [
        lambda: delete(action(preset, task.id)),
        lambda: preset.make(GetTask)(action(preset, task.id)),
        lambda: preset.make(CompleteTask)(action(preset, task.id)),
    ]
    for operation in operations:
        with pytest.raises(TaskNotFoundError):
            await operation()
