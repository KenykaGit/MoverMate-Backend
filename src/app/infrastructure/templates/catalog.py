"""Task templates from a file shipped with the release (docs/db-design.md, decision 10).

The file is read and validated once, when the process starts: a broken catalog stops
the release from starting instead of failing requests later.
"""

import re
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.application.interfaces.task_templates import AbstractTaskTemplateCatalog
from app.domain.tasks.value_objects import (
    TaskDescription,
    TaskPhase,
    TaskTemplate,
    TaskTemplateId,
    TaskTitle,
)

TASK_TEMPLATES_FILE = Path(__file__).with_name("task_templates.toml")

_ID_FORMAT = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_PHASES = list(TaskPhase)


class InvalidTaskTemplatesError(ValueError):
    """The templates file breaks its format: the release must not start."""


class FileTaskTemplateCatalog(AbstractTaskTemplateCatalog):
    def __init__(self, path: Path = TASK_TEMPLATES_FILE) -> None:
        self._templates = load_task_templates(path)

    def all(self) -> Sequence[TaskTemplate]:
        return self._templates


def load_task_templates(path: Path) -> tuple[TaskTemplate, ...]:
    """Templates of the file, by phase of the move, in file order within a phase.

    Raises:
        InvalidTaskTemplatesError: a malformed file, an id that is not a slug or
            appears twice, an unknown phase; DomainError: a title or description
            out of the rules of a task.
    """
    try:
        entries = tomllib.loads(path.read_text(encoding="utf-8"))["templates"]
    except (tomllib.TOMLDecodeError, KeyError) as error:
        raise InvalidTaskTemplatesError(f"{path.name}: {error}") from error

    templates = [_template(entry, path) for entry in entries]
    ids = [template.id for template in templates]
    duplicates = sorted({template_id for template_id in ids if ids.count(template_id) > 1})
    if duplicates:
        raise InvalidTaskTemplatesError(f"{path.name}: duplicate ids {duplicates}")
    # sorted() is stable: the file order is kept within a phase.
    return tuple(sorted(templates, key=lambda template: _PHASES.index(template.phase)))


def _template(entry: dict[str, Any], path: Path) -> TaskTemplate:
    template_id = entry.get("id", "")
    if not isinstance(template_id, str) or not _ID_FORMAT.fullmatch(template_id):
        raise InvalidTaskTemplatesError(f"{path.name}: id {template_id!r} is not a slug")
    try:
        phase = TaskPhase(entry["phase"])
    except (KeyError, ValueError) as error:
        raise InvalidTaskTemplatesError(f"{path.name}: {template_id}: bad phase") from error
    description = entry.get("description")
    return TaskTemplate(
        id=TaskTemplateId(template_id),
        title=TaskTitle(entry.get("title", "")),
        phase=phase,
        description=None if description is None else TaskDescription(description),
    )
