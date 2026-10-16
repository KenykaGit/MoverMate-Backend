from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.domain.common.ids import UserId
from app.domain.tokens.entity import RefreshToken, RefreshTokenId, TokenFamilyId
from app.infrastructure.db.mappers.refresh_token import RefreshTokenMapper
from app.infrastructure.db.models.refresh_token import RefreshTokenModel


class SqlAlchemyRefreshTokenRepository(AbstractRefreshTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> None:
        self._session.add(RefreshTokenMapper.to_model(token))

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        model = await self._session.scalar(
            select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash)
        )
        return None if model is None else RefreshTokenMapper.to_domain(model)

    async def revoke(self, token_id: RefreshTokenId, now: datetime) -> bool:
        # UPDATE ... WHERE revoked_at IS NULL RETURNING id is atomic: a concurrent
        # transaction revoking the same row waits for this one, then matches nothing.
        result = await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.id == token_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now)
            .returning(RefreshTokenModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def revoke_family(self, family_id: TokenFamilyId, now: datetime) -> int:
        result = await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.family_id == family_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now)
            .returning(RefreshTokenModel.id)
        )
        return len(result.all())

    async def revoke_all_for_user(self, user_id: UserId, now: datetime) -> None:
        await self._session.execute(
            update(RefreshTokenModel)
            .where(RefreshTokenModel.user_id == user_id, RefreshTokenModel.revoked_at.is_(None))
            .values(revoked_at=now)
        )
