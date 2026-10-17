from app.domain.users.entity import User
from app.domain.users.errors import (
    InvalidPasswordError,
    InvalidPasswordHashError,
    InvalidUsernameError,
)
from app.domain.users.value_objects import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    USERNAME_MAX_LENGTH,
    PasswordHash,
    RawPassword,
    Username,
)
from tests.checks import Rejection, not_rejected
from tests.factories import make_user


class TestUsername:
    def test_is_normalized_to_trimmed_lowercase(self) -> None:
        assert Username("  John_Doe ").value == "john_doe"

    def test_rejects_wrong_length_and_characters(self) -> None:
        values = ["ab", "a" * (USERNAME_MAX_LENGTH + 1), "john@doe", "john doe", "джон", "john!"]
        values.append("\N{KELVIN SIGN}elvin")  # lowercases to the ASCII "kelvin"

        assert not_rejected((Username, value, InvalidUsernameError) for value in values) == []


class TestPasswords:
    def test_reject_values_out_of_their_rules(self) -> None:
        cases: list[Rejection] = [
            (RawPassword, "a" * (PASSWORD_MIN_LENGTH - 1), InvalidPasswordError),
            (RawPassword, "a" * (PASSWORD_MAX_LENGTH + 1), InvalidPasswordError),
            (RawPassword, " " * PASSWORD_MIN_LENGTH, InvalidPasswordError),
            (PasswordHash, "", InvalidPasswordHashError),
            (PasswordHash, "   ", InvalidPasswordHashError),
        ]

        assert not_rejected(cases) == []

    def test_raw_password_is_kept_as_is_but_hidden_from_repr(self) -> None:
        assert RawPassword(" secret password ").value == " secret password "
        assert "hunter22" not in repr(RawPassword("hunter2222"))
        assert "secret-hash" not in repr(PasswordHash("secret-hash"))


class TestUser:
    def test_register_creates_active_user_with_new_id(self) -> None:
        first, second = make_user(), make_user()

        assert first.is_active
        assert first.id != second.id

    def test_soft_delete_and_restore_are_idempotent(self) -> None:
        user = make_user()

        user.soft_delete()
        user.soft_delete()
        assert not user.is_active

        user.restore()
        user.restore()
        assert user.is_active

    def test_changes(self) -> None:
        user = make_user()

        user.change_username(Username("new_name"))
        user.change_password(PasswordHash("new-hash"))

        assert user.username == Username("new_name")
        assert user.password_hash == PasswordHash("new-hash")


def test_entities_are_equal_by_id_only() -> None:
    user = make_user()
    changed = User(
        id=user.id,
        username=Username("other"),
        password_hash=PasswordHash("other"),
        is_active=False,
    )

    assert user == changed
    assert hash(user) == hash(changed)
    assert len({user, changed}) == 1
    assert user != make_user()
    assert user != user.id
    assert user != object()
