from dataclasses import dataclass, field

from app.application.seed.catalog import TEST_PASSWORD


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedTestUsersDTO:
    password: str = field(default=TEST_PASSWORD, repr=False)
    """The password of every test account."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SeedTestUsersResultDTO:
    created: tuple[str, ...]
    """Usernames registered by this run, with their projects."""
    skipped: tuple[str, ...]
    """Usernames that already existed: left exactly as they are."""
