from datetime import UTC, date, datetime

import pytest

from app.domain.common.ids import UserId, new_id
from app.domain.common.reminders import MovingReminderOffset
from app.domain.projects.errors import (
    CannotRemoveOwnerError,
    InvalidAddressError,
    InvalidProjectNameError,
    InvalidTimeZoneError,
    NotProjectOwnerError,
)
from app.domain.projects.value_objects import (
    ADDRESS_MAX_LENGTH,
    PROJECT_NAME_MAX_LENGTH,
    Address,
    ProjectName,
    QrAccess,
    QrCodeStrategy,
    TimeZone,
)
from tests.checks import Rejection, not_rejected
from tests.factories import make_project


class TestValueObjects:
    def test_name_is_trimmed_and_nfc_normalized(self) -> None:
        # "й" as "и" + combining breve: NFC makes it the single precomposed letter.
        assert ProjectName("  Мой переезд ").value == "Мой переезд"

    def test_reject_values_out_of_their_rules(self) -> None:
        bad_zones = ["", "Moscow", "europe/moscow", "Europe/Moscow ", "../etc/passwd", "+03:00"]
        cases: list[Rejection] = [
            (ProjectName, "", InvalidProjectNameError),
            (ProjectName, "   ", InvalidProjectNameError),
            (ProjectName, "a" * (PROJECT_NAME_MAX_LENGTH + 1), InvalidProjectNameError),
            (Address, "  ", InvalidAddressError),
            (Address, "a" * (ADDRESS_MAX_LENGTH + 1), InvalidAddressError),
            *((TimeZone, zone, InvalidTimeZoneError) for zone in bad_zones),
        ]

        assert not_rejected(cases) == []

    def test_time_zone_accepts_iana_names(self) -> None:
        names = ["UTC", "Europe/Moscow", "Asia/Vladivostok"]

        assert [TimeZone(name).zone.key for name in names] == names


class TestMembers:
    def test_owner_is_the_first_member(self) -> None:
        project = make_project()

        assert project.member_ids == {project.owner_id}
        assert project.qr_code_strategy is QrCodeStrategy.PROTECTED
        assert project.is_active

    def test_add_and_remove_are_idempotent_but_the_owner_stays(self) -> None:
        project = make_project()
        member = UserId(new_id())

        project.add_member(member)
        project.add_member(member)
        assert project.is_member(member)

        project.remove_member(member)
        project.remove_member(member)
        assert not project.is_member(member)
        with pytest.raises(CannotRemoveOwnerError):
            project.remove_member(project.owner_id)
        assert project.is_member(project.owner_id)


def test_qr_access_matrix() -> None:
    """The access matrix of docs/db-design.md, section 8."""
    matrix = [
        (QrCodeStrategy.PROTECTED, "anonymous", QrAccess.LOGIN_REQUIRED),
        (QrCodeStrategy.PROTECTED, "member", QrAccess.WRITE),
        (QrCodeStrategy.PROTECTED, "stranger", QrAccess.NOT_FOUND),
        (QrCodeStrategy.OPEN, "anonymous", QrAccess.READ),
        (QrCodeStrategy.OPEN, "member", QrAccess.WRITE),
        (QrCodeStrategy.OPEN, "stranger", QrAccess.READ),
    ]
    wrong = []
    for strategy, viewer, expected in matrix:
        project = make_project()
        project.change_qr_code_strategy(strategy)
        member = UserId(new_id())
        project.add_member(member)
        viewers = {"anonymous": None, "member": member, "stranger": UserId(new_id())}
        found = project.qr_access(viewers[viewer])
        if found is not expected:
            wrong.append((strategy, viewer, found))

    assert wrong == []


def test_only_the_owner_deletes_and_idempotently() -> None:
    project = make_project()
    member = UserId(new_id())
    project.add_member(member)

    with pytest.raises(NotProjectOwnerError):
        project.soft_delete(by=member)
    assert project.is_active

    project.soft_delete(by=project.owner_id)
    project.soft_delete(by=project.owner_id)
    assert not project.is_active


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


def test_replacing_moving_reminders_reports_the_difference() -> None:
    project = make_project()
    project.replace_moving_reminders([MovingReminderOffset(7), MovingReminderOffset(1)])

    change = project.replace_moving_reminders(
        [MovingReminderOffset(1), MovingReminderOffset(0), MovingReminderOffset(0)]
    )

    assert change.added == {MovingReminderOffset(0)}
    assert change.removed == {MovingReminderOffset(7)}
    assert project.moving_reminders == {MovingReminderOffset(0), MovingReminderOffset(1)}
    assert not project.replace_moving_reminders(list(project.moving_reminders))


def test_moving_reminder_is_due_at_nine_in_the_project_time_zone() -> None:
    cases = [
        # 09:00 where the move is, whatever the server's zone.
        ("Europe/Moscow", 0, datetime(2026, 11, 20, 6, tzinfo=UTC)),
        ("Europe/Moscow", 7, datetime(2026, 11, 13, 6, tzinfo=UTC)),
        ("Asia/Vladivostok", 1, datetime(2026, 11, 18, 23, tzinfo=UTC)),
        ("UTC", 0, datetime(2026, 11, 20, 9, tzinfo=UTC)),
        # Berlin is on winter time (UTC+1) in November and on summer time (UTC+2) in July.
        ("Europe/Berlin", 0, datetime(2026, 11, 20, 8, tzinfo=UTC)),
        ("Europe/Berlin", 140, datetime(2026, 7, 3, 7, tzinfo=UTC)),
    ]
    wrong = []
    for time_zone, days, expected in cases:
        project = make_project(moving_date=date(2026, 11, 20), time_zone=time_zone)
        found = project.moving_reminder_at(MovingReminderOffset(days))
        if found != expected:
            wrong.append((time_zone, days, found))

    assert wrong == []


def test_rescheduling_and_time_zone_change() -> None:
    project = make_project()

    project.reschedule(date(2027, 1, 1))
    project.change_time_zone(TimeZone("Asia/Yekaterinburg"))

    assert (project.moving_date, project.time_zone.value) == (
        date(2027, 1, 1),
        "Asia/Yekaterinburg",
    )
