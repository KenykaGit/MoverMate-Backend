from datetime import UTC, datetime


def utc_now() -> datetime:
    """The current time; always timezone-aware, so it compares with stored values."""
    return datetime.now(UTC)
