from app.domain.audit.entity import AuditAction, AuditEntityType, AuditEntry
from app.domain.common.ids import ProjectId, new_id
from tests.factories import NOW


def test_every_action_names_its_entity_type() -> None:
    assert [a for a in AuditAction if a.entity_type not in AuditEntityType] == []


def test_record_copies_details() -> None:
    details = {"from": "PACKED", "to": "LOADED"}
    box_id = new_id()

    entry = AuditEntry.record(
        project_id=ProjectId(new_id()),
        actor_id=None,
        action=AuditAction.BOX_STATUS_CHANGED,
        entity_id=box_id,
        now=NOW,
        details=details,
    )
    details["to"] = "DELIVERED"

    assert entry.entity_type is AuditEntityType.BOX
    assert entry.entity_id == box_id
    assert entry.details == {"from": "PACKED", "to": "LOADED"}
    assert entry.created_at == NOW
