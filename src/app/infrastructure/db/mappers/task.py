from app.domain.common.ids import ProjectId, TaskId, UserId
from app.domain.common.reminders import ReminderOffset
from app.domain.tasks.entity import Task
from app.domain.tasks.value_objects import TaskDescription, TaskTitle
from app.infrastructure.db.models.task import TaskCollaboratorModel, TaskModel, TaskReminderModel


class TaskMapper:
    """Converts between the domain Task (with assignees and reminders) and its ORM rows."""

    @staticmethod
    def to_domain(model: TaskModel) -> Task:
        return Task(
            id=TaskId(model.id),
            project_id=ProjectId(model.project_id),
            title=TaskTitle(model.title),
            description=None if model.description is None else TaskDescription(model.description),
            due_at=model.due_at,
            done_at=model.done_at,
            assignee_ids={UserId(row.user_id) for row in model.assignees},
            reminders={ReminderOffset(row.offset_hours) for row in model.reminders},
            is_active=model.is_active,
        )

    @staticmethod
    def to_model(task: Task) -> TaskModel:
        # Sorted: a stable order of rows, whatever the set iteration order.
        return TaskModel(
            id=task.id,
            project_id=task.project_id,
            title=task.title.value,
            description=None if task.description is None else task.description.value,
            due_at=task.due_at,
            is_done=task.is_done,
            done_at=task.done_at,
            is_active=task.is_active,
            assignees=[
                TaskCollaboratorModel(task_id=task.id, project_id=task.project_id, user_id=user_id)
                for user_id in sorted(task.assignee_ids)
            ],
            reminders=[
                TaskReminderModel(task_id=task.id, offset_hours=offset.hours)
                for offset in sorted(task.reminders)
            ],
        )
