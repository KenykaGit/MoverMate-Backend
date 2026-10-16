from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.application.interfaces.health import AbstractHealthProbe


class DatabaseHealthProbe(AbstractHealthProbe):
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
