import pytest

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
from tests.factories import make_user


class TestUsername:
    def test_is_normalized_to_trimmed_lowercase(self) -> None:
        assert Username("  John_Doe ").value == "john_doe"

    @pytest.mark.parametrize("value", ["ab", "a" * (USERNAME_MAX_LENGTH + 1)])
    def test_rejects_wrong_length(self, value: str) -> None:
        with pytest.raises(InvalidUsernameError):
            Username(value)

    @pytest.mark.parametrize("value", ["john@doe", "john doe", "джон", "john!"])
    def test_rejects_forbidden_characters(self, value: str) -> None:
        with pytest.raises(InvalidUsernameError):
            Username(value)

    def test_rejects_non_ascii_that_lowercases_to_ascii(self) -> None:
        with pytest.raises(InvalidUsernameError):
            Username("\N{KELVIN SIGN}elvin")  # lowercases to "kelvin"


class TestRawPassword:
    @pytest.mark.parametrize(
        "value", ["a" * (PASSWORD_MIN_LENGTH - 1), "a" * (PASSWORD_MAX_LENGTH + 1)]
    )
    def test_rejects_wrong_length(self, value: str) -> None:
        with pytest.raises(InvalidPasswordError):
            RawPassword(value)

    def test_rejects_whitespace_only(self) -> None:
        with pytest.raises(InvalidPasswordError):
            RawPassword(" " * PASSWORD_MIN_LENGTH)

    def test_keeps_surrounding_whitespace(self) -> None:
        assert RawPassword(" secret password ").value == " secret password "

    def test_is_hidden_from_repr(self) -> None:
        assert "hunter22" not in repr(RawPassword("hunter2222"))


class TestPasswordHash:
    @pytest.mark.parametrize("value", ["", "   "])
    def test_rejects_empty(self, value: str) -> None:
        with pytest.raises(InvalidPasswordHashError):
            PasswordHash(value)

    def test_is_hidden_from_repr(self) -> None:
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


class TestEntityIdentity:
    def test_equal_by_id_regardless_of_attributes(self) -> None:
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

    def test_different_ids_are_different_entities(self) -> None:
        assert make_user() != make_user()

    def test_not_equal_to_other_types(self) -> None:
        user = make_user()

        assert user != user.id
        assert user != object()
