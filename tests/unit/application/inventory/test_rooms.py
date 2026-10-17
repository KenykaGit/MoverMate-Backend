import pytest

from app.application.exceptions import (
    ProjectNotFoundError,
    RoomInUseError,
    RoomNameTakenError,
    RoomNotFoundError,
)
from app.application.inventory.create_room import CreateRoom
from app.application.inventory.delete_room import DeleteRoom
from app.application.inventory.dto import CreateRoomDTO, DeleteRoomDTO, ListRoomsDTO, RenameRoomDTO
from app.application.inventory.list_rooms import ListRooms
from app.application.inventory.rename_room import RenameRoom
from app.domain.inventory.errors import InvalidRoomNameError
from app.domain.inventory.value_objects import RoomKind
from tests.unit.application.inventory.conftest import Preset


class TestCreateRoom:
    async def test_adds_a_room_to_the_project(self, preset: Preset) -> None:
        room = await preset.make(CreateRoom)(
            CreateRoomDTO(
                user_id=preset.member.id,
                project_id=preset.project.id,
                kind=RoomKind.SOURCE,
                name=" Спальня ",
            )
        )

        assert (room.kind, room.name) == (RoomKind.SOURCE, "Спальня")
        assert room.id in preset.rooms.rooms
        assert preset.uow.commits == 1

    async def test_strangers_do_not_see_the_project(self, preset: Preset) -> None:
        with pytest.raises(ProjectNotFoundError):
            await preset.make(CreateRoom)(
                CreateRoomDTO(
                    user_id=preset.stranger.id,
                    project_id=preset.project.id,
                    kind=RoomKind.SOURCE,
                    name="Спальня",
                )
            )
        assert preset.uow.commits == 0

    async def test_a_name_is_unique_per_kind(self, preset: Preset) -> None:
        with pytest.raises(RoomNameTakenError):
            await preset.make(CreateRoom)(
                CreateRoomDTO(
                    user_id=preset.owner.id,
                    project_id=preset.project.id,
                    kind=RoomKind.SOURCE,
                    name="Кухня",
                )
            )
        assert preset.uow.commits == 0

    async def test_rejects_a_bad_name_before_anything(self, preset: Preset) -> None:
        with pytest.raises(InvalidRoomNameError):
            await preset.make(CreateRoom)(
                CreateRoomDTO(
                    user_id=preset.stranger.id,  # not even checked yet
                    project_id=preset.project.id,
                    kind=RoomKind.SOURCE,
                    name="   ",
                )
            )


async def test_list_rooms_by_kind(preset: Preset) -> None:
    list_rooms = preset.make(ListRooms)
    owner, project = preset.owner.id, preset.project.id

    sources = await list_rooms(
        ListRoomsDTO(user_id=owner, project_id=project, kind=RoomKind.SOURCE)
    )
    both = await list_rooms(ListRoomsDTO(user_id=owner, project_id=project))

    assert [room.id for room in sources] == [preset.source.id]
    assert {room.id for room in both} == {preset.source.id, preset.destination.id}


class TestRenameRoom:
    async def test_renames(self, preset: Preset) -> None:
        room = await preset.make(RenameRoom)(
            RenameRoomDTO(user_id=preset.owner.id, room_id=preset.source.id, name="Кладовка")
        )

        assert room.name == "Кладовка"
        assert preset.rooms.rooms[preset.source.id].name.value == "Кладовка"

    async def test_rooms_of_other_projects_are_not_found(self, preset: Preset) -> None:
        with pytest.raises(RoomNotFoundError):
            await preset.make(RenameRoom)(
                RenameRoomDTO(user_id=preset.stranger.id, room_id=preset.source.id, name="Моя")
            )


class TestDeleteRoom:
    async def test_soft_deletes_an_unused_room(self, preset: Preset) -> None:
        await preset.make(DeleteRoom)(
            DeleteRoomDTO(user_id=preset.owner.id, room_id=preset.source.id)
        )

        assert not preset.rooms.rooms[preset.source.id].is_active
        assert preset.uow.commits == 1

    async def test_keeps_a_room_in_use(self, preset: Preset) -> None:
        preset.rooms.referenced.add(preset.source.id)

        with pytest.raises(RoomInUseError):
            await preset.make(DeleteRoom)(
                DeleteRoomDTO(user_id=preset.owner.id, room_id=preset.source.id)
            )

        assert preset.rooms.rooms[preset.source.id].is_active
        assert preset.uow.commits == 0
