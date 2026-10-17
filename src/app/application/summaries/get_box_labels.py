from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.inventory.lookup import project_of_member
from app.application.summaries.dto import BoxLabelDTO, GetBoxLabelsDTO
from app.application.summaries.read_model import AbstractSummaryReadModel
from app.domain.common.ids import BoxId


class GetBoxLabels:
    def __init__(
        self, projects: AbstractProjectRepository, read_model: AbstractSummaryReadModel
    ) -> None:
        self._projects = projects
        self._read_model = read_model

    async def __call__(self, data: GetBoxLabelsDTO) -> list[BoxLabelDTO]:
        """Labels to print, by box number: of every active box, or of the chosen ones.

        Chosen ids that are not active boxes of the project (deleted meanwhile, or
        foreign) are skipped rather than failing the whole sheet.

        Raises:
            ProjectNotFoundError: no such project, or the user is not its member.
        """
        project = await project_of_member(self._projects, data.project_id, data.user_id)
        box_ids = None if data.box_ids is None else {BoxId(box_id) for box_id in data.box_ids}
        return await self._read_model.box_labels(project.id, box_ids)
