from sqlalchemy.exc import IntegrityError


def violated_constraint(error: IntegrityError) -> str | None:
    """Name of the constraint (or unique index) behind an integrity error."""
    # SQLAlchemy wraps the asyncpg exception, which carries the constraint name.
    driver_error = getattr(error.orig, "__cause__", None)
    name = getattr(driver_error, "constraint_name", None)
    return name if isinstance(name, str) else None
