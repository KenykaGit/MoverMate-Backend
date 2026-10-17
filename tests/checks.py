"""Table-driven checks: one test walks every case and reports all the broken ones.

A parametrized test per case multiplies test ids without adding checks; these
helpers keep one test per rule and still name each failing case.
"""

from collections.abc import Callable, Iterable
from typing import Any

type Rejection = tuple[Callable[[Any], object], object, type[Exception]]


def not_rejected(cases: Iterable[Rejection]) -> list[tuple[str, object]]:
    """The (factory, value) cases that did not raise their expected error."""
    accepted = []
    for factory, value, error in cases:
        try:
            factory(value)
        except error:
            continue
        accepted.append((getattr(factory, "__name__", repr(factory)), value))
    return accepted
