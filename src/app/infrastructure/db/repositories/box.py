from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.exceptions import BoxNotFoundError, BoxStatusConflictError
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.domain.common.ids import BoxId, ProjectId
from app.domain.inventory.box import Box, BoxStatus
from app.domain.inventory.errors import BoxStatusTransitionError
from app.domain.inventory.value_objects import BoxNumber, QrToken
from app.infrastructure.db.errors import violated_constraint
from app.infrastructure.db.mappers.inventory import BoxMapper
from app.infrastructure.db.models.box import BOX_STATUS_TRANSITION_CONSTRAINT, BoxModel

# The lowest positive number no active box has (docs/db-design.md, section 6).
_NEXT_FREE_NUMBER = text(
    """
    SELECT s.n
    FROM generate_series(
        1,
        (SELECT COALESCE(MAX(number), 0) + 1 FROM boxes WHERE project_id = :p AND is_active)
    ) AS s(n)
    WHERE NOT EXISTS (
        SELECT 1 FROM boxes WHERE project_id = :p AND is_active AND number = s.n
    )
    ORDER BY s.n
    LIMIT 1
    """
)


class SqlAlchemyBoxRepository(AbstractBoxRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, box: Box) -> None:
        self._session.add(BoxMapper.to_model(box))
        # Right away: items inserted next refer to the box.
        await self._session.flush()

    async def get(self, box_id: BoxId) -> Box | None:
        model = await self._session.get(BoxModel, box_id)
        if model is None or not model.is_active:
            return None
        return BoxMapper.to_domain(model)

    async def get_by_qr_token(self, qr_token: QrToken) -> Box | None:
        model = await self._session.scalar(
            select(BoxModel).where(BoxModel.qr_token == qr_token.value, BoxModel.is_active)
        )
        return None if model is None else BoxMapper.to_domain(model)

    async def next_free_number(self, project_id: ProjectId) -> BoxNumber:
        number = await self._session.scalar(_NEXT_FREE_NUMBER, {"p": project_id})
        return BoxNumber(int(number or 1))

    async def update(self, box: Box) -> None:
        model = await self._session.get(BoxModel, box.id)
        if model is None:
            raise BoxNotFoundError(box.id)
        # Not the status: change_status alone writes it, conditionally.
        model.title = None if box.title is None else box.title.value
        model.is_fragile = box.is_fragile
        model.destination_room_id = box.destination_room_id
        model.is_active = box.is_active
        await self._session.flush()

    async def change_status(self, box: Box, *, expected: BoxStatus) -> None:
        # Atomic compare-and-set: a concurrent change makes the WHERE match nothing.
        try:
            async with self._session.begin_nested():
                result = await self._session.execute(
                    update(BoxModel)
                    .where(
                        BoxModel.id == box.id,
                        BoxModel.status == expected.value,
                        BoxModel.is_active,
                    )
                    .values(status=box.status.value, status_changed_at=box.status_changed_at)
                    .returning(BoxModel.id)
                )
                changed = result.scalar_one_or_none() is not None
        except IntegrityError as error:
            if violated_constraint(error) == BOX_STATUS_TRANSITION_CONSTRAINT:
                raise BoxStatusTransitionError(
                    f"A box cannot go from {expected} to {box.status}"
                ) from error
            raise
        if not changed:
            raise BoxStatusConflictError(
                f"Box {box.id} is no longer {expected}: it was changed meanwhile"
            )
