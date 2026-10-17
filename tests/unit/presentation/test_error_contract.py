"""Every error the inner layers can raise has a stable code (checked statically)."""

import importlib
import pkgutil

import app.application
import app.domain
from app.application.exceptions import ApplicationError, UserNotFoundError
from app.domain.common.errors import DomainError
from app.domain.common.ids import new_id
from app.presentation.http.errors import _INTERNAL_ERRORS, ERROR_SPECS, _spec_for


def _import_all(package: object) -> None:
    for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):  # type: ignore[attr-defined]
        importlib.import_module(module.name)


def _subclasses(base: type[Exception]) -> set[type[Exception]]:
    found: set[type[Exception]] = set()
    pending = [base]
    while pending:
        for cls in pending.pop().__subclasses__():
            if cls not in found:
                found.add(cls)
                pending.append(cls)
    return found


def test_every_concrete_error_has_its_own_code() -> None:
    _import_all(app.domain)
    _import_all(app.application)
    errors = _subclasses(DomainError) | _subclasses(ApplicationError)
    abstract = {cls for cls in errors if cls.__subclasses__()}

    unmapped = {
        cls.__qualname__
        for cls in errors - abstract
        if cls not in ERROR_SPECS and not issubclass(cls, _INTERNAL_ERRORS)
    }

    assert not unmapped, f"add these to ERROR_SPECS: {sorted(unmapped)}"


def test_codes_are_unique() -> None:
    codes = [spec.code for spec in ERROR_SPECS.values()]

    assert len(codes) == len(set(codes))


def test_most_specific_spec_wins() -> None:
    spec = _spec_for(UserNotFoundError(new_id()))  # a NotFoundError ("not_found") too

    assert spec is not None
    assert spec.code == "user_not_found"
