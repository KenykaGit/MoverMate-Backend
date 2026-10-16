from app.domain.common.ids import UserId
from app.domain.users.entity import User
from app.domain.users.value_objects import PasswordHash, Username
from app.infrastructure.db.models.user import UserModel


class UserMapper:
    """Converts between the domain User and its ORM representation."""

    @staticmethod
    def to_domain(model: UserModel) -> User:
        return User(
            id=UserId(model.id),
            username=Username(model.username),
            password_hash=PasswordHash(model.password_hash),
            is_active=model.is_active,
        )

    @staticmethod
    def to_model(user: User) -> UserModel:
        return UserModel(
            id=user.id,
            username=user.username.value,
            password_hash=user.password_hash.value,
            is_active=user.is_active,
        )
