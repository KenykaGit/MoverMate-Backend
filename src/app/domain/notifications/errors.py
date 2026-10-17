from app.domain.common.errors import DomainError


class InvalidPushSubscriptionError(DomainError):
    """The Web Push subscription sent by the browser is malformed."""


class NotificationAlreadyFinishedError(DomainError):
    """Only a pending notification can be sent or retried.

    A bug in the worker, never the client's fault.
    """
