from app.application.exceptions import BoxNotFoundError, NotAuthenticatedError
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.dto import (
    BoxByQrDTO,
    GetBoxByQrDTO,
    ProjectRefDTO,
    QrAccessLevel,
)
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.domain.common.ids import UserId
from app.domain.inventory.errors import InvalidQrTokenError
from app.domain.inventory.value_objects import QrToken
from app.domain.projects.value_objects import QrAccess

_LEVELS = {QrAccess.READ: QrAccessLevel.READ, QrAccess.WRITE: QrAccessLevel.WRITE}


class GetBoxByQr:
    def __init__(
        self,
        projects: AbstractProjectRepository,
        boxes: AbstractBoxRepository,
        read_model: AbstractInventoryReadModel,
    ) -> None:
        self._projects = projects
        self._boxes = boxes
        self._read_model = read_model

    async def __call__(self, data: GetBoxByQrDTO) -> BoxByQrDTO:
        """The card of a box opened by its QR label, and what the caller may do with it.

        The project's QR strategy is checked now, so switching it applies to labels
        already printed. PROTECTED: members get WRITE, others do not learn the box
        exists. OPEN: members get WRITE, everyone else READ.

        Raises:
            NotAuthenticatedError: PROTECTED project and an anonymous caller.
            BoxNotFoundError: no active box with this token, or a PROTECTED project
                and a caller who is not a member.
        """
        try:
            token = QrToken(data.qr_token)
        except InvalidQrTokenError:
            # A malformed token is just a token of no box: no hint about the format.
            raise BoxNotFoundError(data.qr_token) from None
        box = await self._boxes.get_by_qr_token(token)
        project = None if box is None else await self._projects.get_by_id(box.project_id)
        if box is None or project is None:
            raise BoxNotFoundError(data.qr_token)

        viewer = None if data.user_id is None else UserId(data.user_id)
        access = project.qr_access(viewer)
        if access is QrAccess.LOGIN_REQUIRED:
            raise NotAuthenticatedError
        if access is QrAccess.NOT_FOUND:
            raise BoxNotFoundError(data.qr_token)

        card = await self._read_model.qr_box(box.id)
        if card is None:
            raise BoxNotFoundError(data.qr_token)
        return BoxByQrDTO(
            access=_LEVELS[access],
            project=ProjectRefDTO(id=project.id, name=project.name.value),
            box=card,
        )
