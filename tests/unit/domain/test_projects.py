import pytest

from app.domain.common.ids import UserId, new_id
from app.domain.common.reminders import MovingReminderOffset
from app.domain.projects.errors import (
    CannotRemoveOwnerError,
    InvalidAddressError,
    InvalidProjectNameError,
    NotProjectOwnerError,
)
from app.domain.projects.value_objects import (
    ADDRESS_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
    Address,
    ProjectName,
    QrAccess,
    QrCodeStrategy,
)
from tests.factories import make_project


class TestValueObjects:
    def test_name_is_trimmed_and_nfc_normalized(self) -> None:
        # "й" as "и" + combining breve: NFC makes it the single precomposed letter.
        assert ProjectName("  Мой переезд ").value == "Мой переезд"

    @pytest.mark.parametrize("value", ["", "   ", "a" * (PROJECT_NAME_MAX_LENGTH + 1)])
    def test_name_rejects_wrong_length(self, value: str) -> None:
        with pytest.raises(InvalidProjectNameError):
            ProjectName(value)

    @pytest.mark.parametrize("value", ["  ", "a" * (ADDRESS_MAX_LENGTH + 1)])
    def test_address_rejects_wrong_length(self, value: str) -> None:
        with pytest.raises(InvalidAddressError):
            Address(value)


class TestMembers:
    def test_owner_is_the_first_member(self) -> None:
        project = make_project()

        assert project.member_ids == {project.owner_id}
        assert project.qr_code_strategy is QrCodeStrategy.PROTECTED
        assert project.is_active

    def test_add_and_remove_are_idempotent(self) -> None:
        project = make_project()
        member = UserId(new_id())

        project.add_member(member)
        project.add_member(member)
        assert project.is_member(member)

        project.remove_member(member)
        project.remove_member(member)
        assert not project.is_member(member)

    def test_owner_cannot_be_removed(self) -> None:
        project = make_project()

        with pytest.raises(CannotRemoveOwnerError):
            project.remove_member(project.owner_id)
        assert project.is_member(project.owner_id)


class TestQrAccess:
    """The access matrix of docs/db-design.md, section 8."""

    @pytest.mark.parametrize(
        ("strategy", "viewer", "expected"),
        [
            (QrCodeStrategy.PROTECTED, "anonymous", QrAccess.LOGIN_REQUIRED),
            (QrCodeStrategy.PROTECTED, "member", QrAccess.WRITE),
            (QrCodeStrategy.PROTECTED, "stranger", QrAccess.NOT_FOUND),
            (QrCodeStrategy.OPEN, "anonymous", QrAccess.READ),
            (QrCodeStrategy.OPEN, "member", QrAccess.WRITE),
            (QrCodeStrategy.OPEN, "stranger", QrAccess.READ),
        ],
    )
    def test_matrix(self, strategy: QrCodeStrategy, viewer: str, expected: QrAccess) -> None:
        project = make_project()
        project.change_qr_code_strategy(strategy)
        member = UserId(new_id())
        project.add_member(member)
        viewers = {"anonymous": None, "member": member, "stranger": UserId(new_id())}

        assert project.qr_access(viewers[viewer]) is expected


class TestDeletion:
    def test_by_the_owner_is_idempotent(self) -> None:
        project = make_project()

        project.soft_delete(by=project.owner_id)
        project.soft_delete(by=project.owner_id)

        assert not project.is_active

    def test_other_members_cannot_delete(self) -> None:
        project = make_project()
        member = UserId(new_id())
        project.add_member(member)

        with pytest.raises(NotProjectOwnerError):
            project.soft_delete(by=member)
        assert project.is_active


def test_moving_reminders_report_changes() -> None:
    project = make_project()
    week = MovingReminderOffset(7)
    assert project.moving_reminders == set()

    assert project.add_moving_reminder(week)
    assert not project.add_moving_reminder(week)
    assert project.add_moving_reminder(MovingReminderOffset(1))
    assert project.remove_moving_reminder(week)
    assert not project.remove_moving_reminder(week)

    assert project.moving_reminders == {MovingReminderOffset(1)}
