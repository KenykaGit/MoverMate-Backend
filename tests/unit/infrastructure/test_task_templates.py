from pathlib import Path

import pytest

from app.domain.common.errors import DomainError
from app.domain.tasks.value_objects import TaskPhase
from app.infrastructure.templates.catalog import (
    TASK_TEMPLATES_FILE,
    FileTaskTemplateCatalog,
    InvalidTaskTemplatesError,
    load_task_templates,
)


def test_the_shipped_catalog_is_valid_and_ordered_by_phase() -> None:
    templates = FileTaskTemplateCatalog().all()

    phases = [template.phase for template in templates]
    assert phases == sorted(phases, key=list(TaskPhase).index)
    assert set(phases) == set(TaskPhase), "every column of the plan has templates"
    # The example of api-endpoints.md, section 8: clients may already use it.
    assert "notify-management-company" in {template.id for template in templates}


def test_a_broken_file_stops_the_release(tmp_path: Path) -> None:
    entry = 'id = "pack"\ntitle = "Pack"\nphase = "BEFORE"\n'
    files = {
        "not toml": "[[templates]\n",
        "no templates": "title = 1\n",
        "id not a slug": '[[templates]]\nid = "Pack It"\ntitle = "Pack"\nphase = "BEFORE"\n',
        "unknown phase": '[[templates]]\nid = "pack"\ntitle = "Pack"\nphase = "LATER"\n',
        "duplicate id": f"[[templates]]\n{entry}[[templates]]\n{entry}",
        "empty title": '[[templates]]\nid = "pack"\ntitle = " "\nphase = "BEFORE"\n',
    }
    accepted = []
    for case, content in files.items():
        path = tmp_path / f"{case}.toml"
        path.write_text(content, encoding="utf-8")
        try:
            load_task_templates(path)
        except InvalidTaskTemplatesError, DomainError:
            continue
        accepted.append(case)

    assert accepted == []
    with pytest.raises(FileNotFoundError):
        load_task_templates(tmp_path / "missing.toml")
    assert TASK_TEMPLATES_FILE.exists()
