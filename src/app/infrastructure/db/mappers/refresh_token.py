from app.domain.common.ids import UserId
from app.domain.tokens.entity import RefreshToken, RefreshTokenId, TokenFamilyId
from app.infrastructure.db.models.refresh_token import RefreshTokenModel


class RefreshTokenMapper:
    """Converts between the domain RefreshToken and its ORM representation."""

    @staticmethod
    def to_domain(model: RefreshTokenModel) -> RefreshToken:
        return RefreshToken(
            id=RefreshTokenId(model.id),
            user_id=UserId(model.user_id),
            family_id=TokenFamilyId(model.family_id),
            token_hash=model.token_hash,
            expires_at=model.expires_at,
            revoked_at=model.revoked_at,
        )

    @staticmethod
    def to_model(token: RefreshToken) -> RefreshTokenModel:
        return RefreshTokenModel(
            id=token.id,
            user_id=token.user_id,
            family_id=token.family_id,
            token_hash=token.token_hash,
            expires_at=token.expires_at,
            revoked_at=token.revoked_at,
        )
