from typing import Any

from app.presentation.http.middleware import UNMATCHED_ROUTE, route_template


def scope(path: str, params: dict[str, Any] | None = None, *, matched: bool = True) -> Any:
    return {"path": path, "path_params": params or {}, "route": object() if matched else None}


def test_replaces_path_params_with_their_names() -> None:
    item_id = "0191c6a2-7d0b-7c3e-9a1b-2f0e5d4c3b2a"

    template = route_template(scope(f"/api/v1/items/{item_id}", {"item_id": item_id}))

    assert template == "/api/v1/items/{item_id}"


def test_static_paths_are_kept() -> None:
    assert route_template(scope("/api/v1/users/me")) == "/api/v1/users/me"


def test_unmatched_paths_collapse_into_one_label() -> None:
    assert route_template(scope("/wp-admin/login.php", matched=False)) == UNMATCHED_ROUTE
