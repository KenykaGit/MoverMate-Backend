from dishka import Provider, Scope, provide_all

from app.application.auth.authenticate_user import AuthenticateUser
from app.application.auth.login import Login
from app.application.auth.logout import Logout
from app.application.auth.refresh_tokens import RefreshTokens
from app.application.auth.services import CredentialsVerifier, TokenPairIssuer
from app.application.common.idempotency import IdempotentExecutor
from app.application.inventory.cancel_contents_reminder import CancelContentsReminder
from app.application.inventory.change_box_status import ChangeBoxStatus
from app.application.inventory.change_item_state import ChangeItemState
from app.application.inventory.create_box import CreateBox
from app.application.inventory.create_item import CreateItem
from app.application.inventory.create_items import CreateItems
from app.application.inventory.create_room import CreateRoom
from app.application.inventory.delete_box import DeleteBox
from app.application.inventory.delete_item import DeleteItem
from app.application.inventory.delete_room import DeleteRoom
from app.application.inventory.get_box import GetBox
from app.application.inventory.get_box_by_qr import GetBoxByQr
from app.application.inventory.get_item import GetItem
from app.application.inventory.list_boxes import ListBoxes
from app.application.inventory.list_items import ListItems
from app.application.inventory.list_rooms import ListRooms
from app.application.inventory.rename_room import RenameRoom
from app.application.inventory.schedule_contents_reminder import ScheduleContentsReminder
from app.application.inventory.search_items import SearchItems
from app.application.inventory.suggest_boxes import SuggestBoxes
from app.application.inventory.update_box import UpdateBox
from app.application.inventory.update_item import UpdateItem
from app.application.notifications.deliver_next_notification import DeliverNextNotification
from app.application.notifications.get_vapid_public_key import GetVapidPublicKey
from app.application.notifications.subscribe_to_push import SubscribeToPush
from app.application.notifications.unsubscribe_from_push import UnsubscribeFromPush
from app.application.projects.add_collaborator import AddCollaborator
from app.application.projects.create_project import CreateProject
from app.application.projects.delete_project import DeleteProject
from app.application.projects.get_project import GetProject
from app.application.projects.list_collaborators import ListCollaborators
from app.application.projects.list_projects import ListProjects
from app.application.projects.remove_collaborator import RemoveCollaborator
from app.application.projects.services import MovingReminderScheduler
from app.application.projects.set_project_reminders import SetProjectReminders
from app.application.projects.update_project import UpdateProject
from app.application.users.change_password import ChangePassword
from app.application.users.change_username import ChangeUsername
from app.application.users.get_user import GetUser
from app.application.users.register_user import RegisterUser
from app.application.users.restore_user import RestoreUser
from app.application.users.soft_delete_user import SoftDeleteUser


class UseCaseProvider(Provider):
    """Use cases and application services live per request: they share its
    session and unit of work."""

    scope = Scope.REQUEST

    services = provide_all(
        CredentialsVerifier, TokenPairIssuer, IdempotentExecutor, MovingReminderScheduler
    )

    users = provide_all(
        RegisterUser,
        GetUser,
        ChangePassword,
        ChangeUsername,
        SoftDeleteUser,
        RestoreUser,
    )
    auth = provide_all(Login, RefreshTokens, Logout, AuthenticateUser)
    projects = provide_all(
        CreateProject,
        ListProjects,
        GetProject,
        UpdateProject,
        DeleteProject,
        SetProjectReminders,
        ListCollaborators,
        AddCollaborator,
        RemoveCollaborator,
    )
    notifications = provide_all(
        DeliverNextNotification, GetVapidPublicKey, SubscribeToPush, UnsubscribeFromPush
    )
    inventory = provide_all(
        ListRooms,
        CreateRoom,
        RenameRoom,
        DeleteRoom,
        ListBoxes,
        CreateBox,
        GetBox,
        GetBoxByQr,
        UpdateBox,
        ChangeBoxStatus,
        DeleteBox,
        ScheduleContentsReminder,
        CancelContentsReminder,
        ListItems,
        CreateItem,
        CreateItems,
        GetItem,
        UpdateItem,
        ChangeItemState,
        DeleteItem,
        SearchItems,
        SuggestBoxes,
    )
