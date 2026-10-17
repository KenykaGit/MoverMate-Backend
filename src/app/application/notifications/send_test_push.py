"""TEMPORARY: lets a developer check Web Push end to end. Remove with its route."""

import logging
from uuid import UUID

from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import UserNotFoundError
from app.application.interfaces.push_sender import AbstractPushSender, PushMessage, PushOutcome
from app.application.interfaces.push_subscription_repository import (
    AbstractPushSubscriptionRepository,
)
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.notifications.dto import (
    PushTestFailureDTO,
    PushTestResultDTO,
    SendTestPushDTO,
)
from app.domain.users.value_objects import Username

logger = logging.getLogger(__name__)

TEST_PUSH_TITLE = "Тестовое уведомление"


class SendTestPush:
    def __init__(
        self,
        users: AbstractUserRepository,
        subscriptions: AbstractPushSubscriptionRepository,
        sender: AbstractPushSender,
        uow: AbstractUnitOfWork,
    ) -> None:
        self._users = users
        self._subscriptions = subscriptions
        self._sender = sender
        self._uow = uow

    async def __call__(self, data: SendTestPushDTO, *, requested_by: UUID) -> PushTestResultDTO:
        """Send `data.text` to every active browser of the user.

        A subscription the push service reports gone is deactivated, as the worker
        does. Delivery problems are returned per subscription, never raised.

        Raises:
            InvalidUsernameError: the username is malformed.
            UserNotFoundError: there is no active user with this username.
        """
        username = Username(data.username)
        user = await self._users.get_by_username(username)
        if user is None:
            raise UserNotFoundError(username)

        message = PushMessage(
            title=TEST_PUSH_TITLE, body=data.text, url="/", data={"type": "TEST_PUSH"}
        )
        subscriptions = await self._subscriptions.list_active_for_user(user.id)
        delivered = 0
        errors: list[PushTestFailureDTO] = []
        for subscription in subscriptions:
            result = await self._sender.send(subscription, message)
            if result.outcome is PushOutcome.DELIVERED:
                delivered += 1
                continue
            if result.outcome is PushOutcome.GONE:
                subscription.deactivate()
                await self._subscriptions.update(subscription)
            errors.append(
                PushTestFailureDTO(
                    subscription_id=subscription.id,
                    outcome=result.outcome.value,
                    detail=result.detail,
                )
            )
        await self._uow.commit()

        logger.info(
            "test_push_sent",
            extra={
                "user_id": str(user.id),
                "requested_by": str(requested_by),
                "attempted": len(subscriptions),
                "delivered": delivered,
                "failed": len(errors),
            },
        )
        return PushTestResultDTO(attempted=len(subscriptions), delivered=delivered, errors=errors)
