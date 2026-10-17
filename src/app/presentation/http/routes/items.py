"""Items, search and suggestions for lost items (api-endpoints.md, section 7)."""

from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, status

from app.application.common.pagination import MAX_PAGE_SIZE, CursorPageRequest
from app.application.inventory.change_item_state import ChangeItemState
from app.application.inventory.create_item import CreateItem
from app.application.inventory.create_items import CreateItems
from app.application.inventory.delete_item import DeleteItem
from app.application.inventory.dto import (
    DeleteItemDTO,
    GetItemDTO,
    ListItemsDTO,
    SearchItemsDTO,
    SuggestBoxesDTO,
)
from app.application.inventory.get_item import GetItem
from app.application.inventory.list_items import ListItems
from app.application.inventory.search_items import SearchItems
from app.application.inventory.suggest_boxes import SuggestBoxes
from app.application.inventory.update_item import UpdateItem
from app.domain.inventory.item import ItemState
from app.presentation.http.auth import CurrentUser
from app.presentation.http.errors import error_doc
from app.presentation.http.schemas.inventory import (
    CreateItemRequest,
    CreateItemsRequest,
    Item,
    ItemPage,
    ItemPriority,
    ItemSearchResult,
    ItemSuggestion,
    SetItemStateRequest,
    UpdateItemRequest,
)

PAGE_LIMIT_DEFAULT = 50
SEARCH_LIMIT_DEFAULT = 20

router = APIRouter(
    tags=["items"],
    route_class=DishkaRoute,
    responses=error_doc(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    | error_doc(status.HTTP_404_NOT_FOUND, "No such project, item, box or room, or not a member"),
)


@router.get("/projects/{project_id}/items", operation_id="listItems")
async def list_items(
    project_id: UUID,
    user: CurrentUser,
    list_items: FromDishka[ListItems],
    states: Annotated[
        list[ItemState] | None, Query(alias="state", description="Repeat to match several")
    ] = None,
    priority: ItemPriority | None = None,
    box_id: UUID | None = None,
    source_room_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = PAGE_LIMIT_DEFAULT,
    after: Annotated[str | None, Query(description="next_cursor of the previous page")] = None,
) -> ItemPage:
    """Active items in packing order: priority, then name, then id."""
    page = await list_items(
        ListItemsDTO(
            user_id=user.id,
            project_id=project_id,
            states=tuple(states or ()),
            priority=None if priority is None else priority.to_domain(),
            box_id=box_id,
            source_room_id=source_room_id,
            page=CursorPageRequest(limit=limit, after=after),
        )
    )
    return ItemPage.from_dto(page)


@router.post(
    "/projects/{project_id}/items",
    operation_id="createItem",
    status_code=status.HTTP_201_CREATED,
)
async def create_item(
    project_id: UUID,
    body: CreateItemRequest,
    user: CurrentUser,
    create_item: FromDishka[CreateItem],
) -> Item:
    item = await create_item(body.to_dto(user_id=user.id, project_id=project_id))
    return Item.from_dto(item)


@router.post(
    "/projects/{project_id}/items/batch",
    operation_id="createItems",
    status_code=status.HTTP_201_CREATED,
)
async def create_items(
    project_id: UUID,
    body: CreateItemsRequest,
    user: CurrentUser,
    create_items: FromDishka[CreateItems],
) -> list[Item]:
    """Several items from one room at once: all of them or none."""
    items = await create_items(body.to_dto(user_id=user.id, project_id=project_id))
    return [Item.from_dto(item) for item in items]


@router.get("/projects/{project_id}/items/search", operation_id="searchItems")
async def search_items(
    project_id: UUID,
    user: CurrentUser,
    search_items: FromDishka[SearchItems],
    q: Annotated[str, Query(description="Words matched by prefix; empty finds nothing")],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = SEARCH_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ItemSearchResult:
    """Full-text search by item name, ranked by relevance."""
    found = await search_items(
        SearchItemsDTO(user_id=user.id, project_id=project_id, query=q, limit=limit, offset=offset)
    )
    return ItemSearchResult.from_dto(found)


@router.get("/items/{item_id}", operation_id="getItem")
async def get_item(item_id: UUID, user: CurrentUser, get_item: FromDishka[GetItem]) -> Item:
    item = await get_item(GetItemDTO(user_id=user.id, item_id=item_id))
    return Item.from_dto(item)


@router.patch("/items/{item_id}", operation_id="updateItem")
async def update_item(
    item_id: UUID, body: UpdateItemRequest, user: CurrentUser, update_item: FromDishka[UpdateItem]
) -> Item:
    item = await update_item(body.to_dto(user_id=user.id, item_id=item_id))
    return Item.from_dto(item)


@router.put(
    "/items/{item_id}/state",
    operation_id="setItemState",
    responses=error_doc(status.HTTP_409_CONFLICT, "Not allowed by the item state matrix"),
)
async def set_item_state(
    item_id: UUID,
    body: SetItemStateRequest,
    user: CurrentUser,
    change_item_state: FromDishka[ChangeItemState],
) -> Item:
    """The current state again (the same box for IN_BOX) is 200 without changes."""
    item = await change_item_state(body.to_dto(user_id=user.id, item_id=item_id))
    return Item.from_dto(item)


@router.delete(
    "/items/{item_id}", operation_id="deleteItem", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_item(
    item_id: UUID, user: CurrentUser, delete_item: FromDishka[DeleteItem]
) -> None:
    await delete_item(DeleteItemDTO(user_id=user.id, item_id=item_id))


@router.get("/items/{item_id}/suggestions", operation_id="getItemSuggestions")
async def get_item_suggestions(
    item_id: UUID, user: CurrentUser, suggest_boxes: FromDishka[SuggestBoxes]
) -> list[ItemSuggestion]:
    """Boxes not yet unpacked that hold other items from the same source room,
    most matches first."""
    suggestions = await suggest_boxes(SuggestBoxesDTO(user_id=user.id, item_id=item_id))
    return [ItemSuggestion.from_dto(suggestion) for suggestion in suggestions]
