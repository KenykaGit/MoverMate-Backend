"""In-memory fakes of the application ports: fast, deterministic, inspectable.

Repositories store and return copies, like a real database: a use case that
mutates a loaded entity without calling update() does not "persist" anything,
and the tests catch that.
"""

import asyncio
import copy
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.common.pagination import CursorPage, CursorPageRequest
from app.application.common.uow import AbstractUnitOfWork
from app.application.exceptions import (
    BoxNotFoundError,
    BoxStatusConflictError,
    ItemNotFoundError,
    ProjectNotFoundError,
    RoomNameTakenError,
    RoomNotFoundError,
    UsernameAlreadyTakenError,
    UserNotFoundError,
)
from app.application.interfaces.audit_log_repository import AbstractAuditLogRepository
from app.application.interfaces.box_repository import AbstractBoxRepository
from app.application.interfaces.health import AbstractHealthProbe
from app.application.interfaces.idempotency import (
    AbstractIdempotencyStore,
    BeginOutcome,
    BeginResult,
    IdempotencyRecord,
    IdempotencyState,
    JsonValue,
)
from app.application.interfaces.item_recognizer import (
    AbstractItemRecognizer,
    Photo,
    RecognizedItem,
)
from app.application.interfaces.item_repository import AbstractItemRepository
from app.application.interfaces.notification_context import (
    AbstractNotificationContextReader,
    NotificationContext,
)
from app.application.interfaces.notification_repository import AbstractNotificationRepository
from app.application.interfaces.password_hasher import AbstractPasswordHasher
from app.application.interfaces.project_reader import (
    AbstractProjectReader,
    CollaboratorDTO,
    ProjectDTO,
    UserRefDTO,
)
from app.application.interfaces.project_repository import AbstractProjectRepository
from app.application.interfaces.push_keys import AbstractPushKeys
from app.application.interfaces.push_sender import (
    AbstractPushSender,
    PushMessage,
    PushOutcome,
    PushResult,
)
from app.application.interfaces.push_subscription_repository import (
    AbstractPushSubscriptionRepository,
)
from app.application.interfaces.rate_limiter import AbstractRateLimiter, RateLimitDecision
from app.application.interfaces.refresh_token_repository import AbstractRefreshTokenRepository
from app.application.interfaces.room_repository import AbstractRoomRepository
from app.application.interfaces.tokens import (
    AbstractAccessTokenService,
    AbstractRefreshTokenGenerator,
    IssuedAccessToken,
)
from app.application.interfaces.user_repository import AbstractUserRepository
from app.application.inventory.dto import (
    BoxDTO,
    BoxRefDTO,
    BoxSuggestionDTO,
    ItemDTO,
    ItemSearchDTO,
    QrBoxDTO,
    QrBoxItemDTO,
    RoomRefDTO,
)
from app.application.inventory.dto import UserRefDTO as InventoryUserRefDTO
from app.application.inventory.read_model import AbstractInventoryReadModel
from app.application.summaries.dto import (
    BoxLabelDTO,
    DoneCountDTO,
    LoadingChecklistRoomDTO,
    ProjectSummaryDTO,
    UnpackingProgressRoomDTO,
)
from app.application.summaries.read_model import AbstractSummaryReadModel
from app.domain.audit.entity import AuditAction, AuditEntry
from app.domain.common.ids import BoxId, ItemId, ProjectId, RoomId, UserId
from app.domain.common.reminders import MovingReminderOffset
from app.domain.inventory.box import Box, BoxStatus
from app.domain.inventory.item import Item, ItemState
from app.domain.inventory.room import Room
from app.domain.inventory.value_objects import BoxNumber, Priority, QrToken, RoomKind
from app.domain.notifications.notification import (
    BoxContentsReminder,
    MovingDateReminder,
    Notification,
)
from app.domain.notifications.push_subscription import (
    PushEndpoint,
    PushSubscription,
    PushSubscriptionId,
)
from app.domain.projects.entity import Project
from app.domain.tasks.value_objects import TaskPhase
from app.domain.tokens.entity import RefreshToken, RefreshTokenId, TokenFamilyId
from app.domain.users.entity import User
from app.domain.users.value_objects import PasswordHash, RawPassword, Username


class InMemoryUserRepository(AbstractUserRepository):
    """`users` holds every user, soft-deleted ones included."""

    def __init__(self, users: Iterable[User] = ()) -> None:
        self.users: dict[UserId, User] = {user.id: replace(user) for user in users}

    async def add(self, user: User) -> None:
        self._check_unique(user)
        self.users[user.id] = replace(user)

    async def get_by_id(self, user_id: UserId, *, include_deleted: bool = False) -> User | None:
        user = self.users.get(user_id)
        if user is None or not (user.is_active or include_deleted):
            return None
        return replace(user)

    async def get_by_username(self, username: Username) -> User | None:
        return self._first_active(lambda user: user.username == username)

    async def update(self, user: User) -> None:
        if user.id not in self.users:
            raise UserNotFoundError(user.id)
        self._check_unique(user)
        self.users[user.id] = replace(user)

    def _active(self) -> list[User]:
        return [user for user in self.users.values() if user.is_active]

    def _first_active(self, predicate: Callable[[User], bool]) -> User | None:
        user = next((user for user in self._active() if predicate(user)), None)
        return None if user is None else replace(user)

    def _check_unique(self, user: User) -> None:
        """Mirrors the partial unique indexes: only active users clash."""
        if not user.is_active:
            return
        others = [other for other in self._active() if other.id != user.id]
        if any(other.username == user.username for other in others):
            raise UsernameAlreadyTakenError(user.username)


class InMemoryRefreshTokenRepository(AbstractRefreshTokenRepository):
    def __init__(self, tokens: Iterable[RefreshToken] = ()) -> None:
        self.tokens: dict[RefreshTokenId, RefreshToken] = {t.id: replace(t) for t in tokens}

    async def add(self, token: RefreshToken) -> None:
        self.tokens[token.id] = replace(token)

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        token = next((t for t in self.tokens.values() if t.token_hash == token_hash), None)
        return None if token is None else replace(token)

    async def revoke(self, token_id: RefreshTokenId, now: datetime) -> bool:
        token = self.tokens.get(token_id)
        if token is None or token.is_revoked:
            return False
        token.revoke(now)
        return True

    async def revoke_family(self, family_id: TokenFamilyId, now: datetime) -> int:
        family = [t for t in self.tokens.values() if t.family_id == family_id and not t.is_revoked]
        for token in family:
            token.revoke(now)
        return len(family)

    async def revoke_all_for_user(self, user_id: UserId, now: datetime) -> None:
        for token in self.tokens.values():
            if token.user_id == user_id:
                token.revoke(now)

    def active_for(self, user_id: UserId) -> list[RefreshToken]:
        return [t for t in self.tokens.values() if t.user_id == user_id and not t.is_revoked]


class FakeUnitOfWork(AbstractUnitOfWork):
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakePasswordHasher(AbstractPasswordHasher):
    """Deterministic and fast; counts calls to the expensive operations.

    Hashes "made with outdated parameters" carry another prefix: they verify,
    but needs_rehash() asks for an upgrade.
    """

    PREFIX = "fake-hash:"
    OUTDATED_PREFIX = "fake-hash-outdated:"

    def __init__(self) -> None:
        self.hash_calls = 0
        self.verify_calls = 0
        self.dummy_verify_calls = 0

    @classmethod
    def hash_of(cls, password: str, *, outdated: bool = False) -> PasswordHash:
        """The hash this fake produces: seeds users with known passwords."""
        return PasswordHash((cls.OUTDATED_PREFIX if outdated else cls.PREFIX) + password[::-1])

    async def hash(self, password: RawPassword) -> PasswordHash:
        return await self.rehash(password.value)

    async def rehash(self, password: str) -> PasswordHash:
        self.hash_calls += 1
        return self.hash_of(password)

    async def verify(self, password: str, password_hash: PasswordHash) -> bool:
        self.verify_calls += 1
        return password_hash in {self.hash_of(password), self.hash_of(password, outdated=True)}

    async def dummy_verify(self, password: str) -> None:  # noqa: ARG002
        self.dummy_verify_calls += 1

    def needs_rehash(self, password_hash: PasswordHash) -> bool:
        return password_hash.value.startswith(self.OUTDATED_PREFIX)


class FakeAccessTokenService(AbstractAccessTokenService):
    """Readable tokens `access:<user id>`."""

    PREFIX = "access:"
    TTL = timedelta(minutes=15)

    def issue(self, user_id: UserId) -> IssuedAccessToken:
        return IssuedAccessToken(
            token=f"{self.PREFIX}{user_id}", expires_at=datetime.now(UTC) + self.TTL
        )

    def read_user_id(self, token: str) -> UserId | None:
        if not token.startswith(self.PREFIX):
            return None
        try:
            return UserId(UUID(token.removeprefix(self.PREFIX)))
        except ValueError:
            return None


class FakeRefreshTokenGenerator(AbstractRefreshTokenGenerator):
    """Sequential tokens `refresh-1`, `refresh-2`... with a readable hash."""

    def __init__(self) -> None:
        self._issued = 0

    def generate(self) -> str:
        self._issued += 1
        return f"refresh-{self._issued}"

    def hash(self, token: str) -> str:
        return f"hash:{token}"


class InMemoryIdempotencyStore(AbstractIdempotencyStore):
    """`available = False` simulates a Redis outage (fail-open)."""

    def __init__(self) -> None:
        self.records: dict[str, IdempotencyRecord] = {}
        self.available = True

    async def begin(self, key: str, fingerprint: str) -> BeginResult:
        if not self.available:
            return BeginResult(BeginOutcome.UNAVAILABLE)
        if key in self.records:
            return BeginResult(BeginOutcome.EXISTS, self.records[key])
        self.records[key] = IdempotencyRecord(
            state=IdempotencyState.PENDING, fingerprint=fingerprint
        )
        return BeginResult(BeginOutcome.STARTED)

    async def complete(self, key: str, fingerprint: str, response: JsonValue) -> None:
        if self.available:
            self.records[key] = IdempotencyRecord(
                state=IdempotencyState.COMPLETED, fingerprint=fingerprint, response=response
            )

    async def release(self, key: str) -> None:
        if self.available:
            self.records.pop(key, None)


class InMemoryRateLimiter(AbstractRateLimiter):
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        self.counts[key] = self.counts.get(key, 0) + 1
        count = self.counts[key]
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=max(0, limit - count),
            retry_after=window_seconds,
        )


class FakeHealthProbe(AbstractHealthProbe):
    def __init__(self, *, healthy: bool = True, hangs: bool = False) -> None:
        self.healthy = healthy
        self.hangs = hangs

    async def check(self) -> None:
        if self.hangs:
            await asyncio.Event().wait()
        if not self.healthy:
            raise ConnectionError("dependency is down")


class FakeItemRecognizer(AbstractItemRecognizer):
    """Answers with `items`, or raises `error`; remembers the photos it was shown."""

    def __init__(self, items: Iterable[RecognizedItem] = ()) -> None:
        self.items = list(items)
        self.error: Exception | None = None
        self.photos: list[Photo] = []

    async def recognize(self, photo: Photo) -> list[RecognizedItem]:
        self.photos.append(photo)
        if self.error is not None:
            raise self.error
        return list(self.items)


class InMemoryProjectRepository(AbstractProjectRepository):
    """`projects` holds every project, soft-deleted ones included.

    Like the database, it remembers when each member joined (`joined`), which
    the reader fake reports as the collaborator's created_at.
    """

    def __init__(self, projects: Iterable[Project] = ()) -> None:
        self.projects: dict[ProjectId, Project] = {}
        self.joined: dict[tuple[ProjectId, UserId], datetime] = {}
        self.locks = 0
        self.numbering_locks: list[ProjectId] = []
        for project in projects:
            self._store(project)

    async def add(self, project: Project) -> None:
        self._store(project)

    async def get_by_id(self, project_id: ProjectId, *, for_update: bool = False) -> Project | None:
        project = self.projects.get(project_id)
        if project is None or not project.is_active:
            return None
        self.locks += for_update
        return copy.deepcopy(project)

    async def update(self, project: Project) -> None:
        if project.id not in self.projects:
            raise ProjectNotFoundError(project.id)
        self._store(project)

    async def owns_active_projects(self, user_id: UserId) -> bool:
        return any(p.is_active and p.owner_id == user_id for p in self.projects.values())

    async def lock_for_box_numbering(self, project_id: ProjectId) -> None:
        self.numbering_locks.append(project_id)

    def _store(self, project: Project) -> None:
        for member_id in project.member_ids:
            # Strictly increasing join times: the order of joining is observable.
            self.joined.setdefault(
                (project.id, member_id), _FAKE_EPOCH + timedelta(seconds=len(self.joined))
            )
        for key in [key for key in self.joined if key[0] == project.id]:
            if key[1] not in project.member_ids:
                del self.joined[key]
        self.projects[project.id] = copy.deepcopy(project)


_FAKE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


class InMemoryProjectReader(AbstractProjectReader):
    """Builds the read model from the repository fakes, like the SQL queries do."""

    def __init__(self, projects: InMemoryProjectRepository, users: InMemoryUserRepository) -> None:
        self._projects = projects
        self._users = users

    async def get(self, project_id: ProjectId, *, viewer_id: UserId) -> ProjectDTO | None:
        project = self._projects.projects.get(project_id)
        if project is None or not project.is_active or not project.is_member(viewer_id):
            return None
        return self._dto(project, viewer_id)

    async def list_for_member(self, viewer_id: UserId) -> list[ProjectDTO]:
        visible = [
            p for p in self._projects.projects.values() if p.is_active and p.is_member(viewer_id)
        ]
        visible.sort(key=lambda p: (p.moving_date, p.id))
        return [self._dto(project, viewer_id) for project in visible]

    async def list_collaborators(self, project_id: ProjectId) -> list[CollaboratorDTO]:
        project = self._projects.projects[project_id]
        found = [
            collaborator
            for user_id in project.member_ids
            if (collaborator := self._collaborator(project, user_id)) is not None
        ]
        found.sort(key=lambda c: (not c.is_owner, c.created_at, c.user.id))
        return found

    async def get_collaborator(
        self, project_id: ProjectId, user_id: UserId
    ) -> CollaboratorDTO | None:
        project = self._projects.projects[project_id]
        return self._collaborator(project, user_id) if project.is_member(user_id) else None

    def _collaborator(self, project: Project, user_id: UserId) -> CollaboratorDTO | None:
        user = self._users.users.get(user_id)
        if user is None or not user.is_active:
            return None
        return CollaboratorDTO(
            user=UserRefDTO(id=user.id, username=user.username.value),
            is_owner=user_id == project.owner_id,
            created_at=self._projects.joined[(project.id, user_id)],
        )

    def _dto(self, project: Project, viewer_id: UserId) -> ProjectDTO:
        owner = self._users.users[project.owner_id]
        created_at = self._projects.joined[(project.id, project.owner_id)]
        return ProjectDTO(
            id=project.id,
            name=project.name.value,
            moving_date=project.moving_date,
            origin_address=None if project.origin_address is None else project.origin_address.value,
            destination_address=(
                None if project.destination_address is None else project.destination_address.value
            ),
            qr_code_strategy=project.qr_code_strategy,
            time_zone=project.time_zone.value,
            reminder_offsets_days=tuple(sorted(o.days for o in project.moving_reminders)),
            owner=UserRefDTO(id=owner.id, username=owner.username.value),
            is_owner=viewer_id == project.owner_id,
            created_at=created_at,
            updated_at=created_at,
        )


class InMemoryRoomRepository(AbstractRoomRepository):
    def __init__(self, rooms: Iterable[Room] = ()) -> None:
        self.rooms: dict[RoomId, Room] = {room.id: replace(room) for room in rooms}
        self.referenced: set[RoomId] = set()
        """Rooms is_referenced() reports as used (the fake knows no boxes or items)."""

    async def add(self, room: Room) -> None:
        self._check_unique(room)
        self.rooms[room.id] = replace(room)

    async def get(self, room_id: RoomId) -> Room | None:
        room = self.rooms.get(room_id)
        return None if room is None or not room.is_active else replace(room)

    async def list(self, project_id: ProjectId, kind: RoomKind | None = None) -> list[Room]:
        found = [
            replace(room)
            for room in self.rooms.values()
            if room.is_active and room.project_id == project_id and kind in {None, room.kind}
        ]
        return sorted(found, key=lambda room: (room.name.value, room.id))

    async def update(self, room: Room) -> None:
        if room.id not in self.rooms:
            raise RoomNotFoundError(room.id)
        self._check_unique(room)
        self.rooms[room.id] = replace(room)

    async def is_referenced(self, room_id: RoomId) -> bool:
        return room_id in self.referenced

    def _check_unique(self, room: Room) -> None:
        """Mirrors the partial unique index on (project_id, kind, name) WHERE is_active."""
        key = (room.project_id, room.kind, room.name)
        clash = any(
            other.id != room.id
            and other.is_active
            and (other.project_id, other.kind, other.name) == key
            for other in self.rooms.values()
        )
        if room.is_active and clash:
            raise RoomNameTakenError(room.kind, room.name)


class InMemoryBoxRepository(AbstractBoxRepository):
    def __init__(self, boxes: Iterable[Box] = ()) -> None:
        self.boxes: dict[BoxId, Box] = {box.id: replace(box) for box in boxes}
        self.conflict = False
        """Simulates a concurrent status change: change_status() then fails."""

    async def add(self, box: Box) -> None:
        self.boxes[box.id] = replace(box)

    async def get(self, box_id: BoxId) -> Box | None:
        box = self.boxes.get(box_id)
        return None if box is None or not box.is_active else replace(box)

    async def get_by_qr_token(self, qr_token: QrToken) -> Box | None:
        found = (b for b in self.boxes.values() if b.qr_token == qr_token and b.is_active)
        box = next(found, None)
        return None if box is None else replace(box)

    async def next_free_number(self, project_id: ProjectId) -> BoxNumber:
        taken = {
            box.number.value
            for box in self.boxes.values()
            if box.is_active and box.project_id == project_id
        }
        return BoxNumber(next(n for n in range(1, len(taken) + 2) if n not in taken))

    async def update(self, box: Box) -> None:
        stored = self.boxes.get(box.id)
        if stored is None:
            raise BoxNotFoundError(box.id)
        # Like the real one: everything but the status.
        self.boxes[box.id] = replace(
            box, status=stored.status, status_changed_at=stored.status_changed_at
        )

    async def change_status(self, box: Box, *, expected: BoxStatus) -> None:
        stored = self.boxes.get(box.id)
        if self.conflict or stored is None or stored.status is not expected:
            raise BoxStatusConflictError(str(box.id))
        self.boxes[box.id] = replace(
            stored, status=box.status, status_changed_at=box.status_changed_at
        )


class InMemoryItemRepository(AbstractItemRepository):
    def __init__(self, items: Iterable[Item] = ()) -> None:
        self.items: dict[ItemId, Item] = {item.id: replace(item) for item in items}

    async def add_many(self, items: Sequence[Item]) -> None:
        for item in items:
            self.items[item.id] = replace(item)

    async def get(self, item_id: ItemId) -> Item | None:
        item = self.items.get(item_id)
        return None if item is None or not item.is_active else replace(item)

    async def update(self, item: Item) -> None:
        if item.id not in self.items:
            raise ItemNotFoundError(item.id)
        self.items[item.id] = replace(item)

    async def deactivate_in_box(self, box_id: BoxId) -> int:
        inside = [i for i in self.items.values() if i.box_id == box_id and i.is_active]
        for item in inside:
            item.soft_delete()
        return len(inside)


class InMemoryNotificationRepository(AbstractNotificationRepository):
    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    async def add(self, notification: Notification) -> None:
        self.notifications.append(replace(notification))

    async def claim_next_due(self, now: datetime) -> Notification | None:
        due = [n for n in self.notifications if n.is_pending and n.scheduled_at <= now]
        if not due:
            return None
        return replace(min(due, key=lambda n: (n.scheduled_at, n.id)))

    async def update(self, notification: Notification) -> None:
        index = next(i for i, n in enumerate(self.notifications) if n.id == notification.id)
        self.notifications[index] = replace(notification)

    def get(self, notification: Notification) -> Notification:
        """The stored version of `notification`."""
        return next(n for n in self.notifications if n.id == notification.id)

    async def cancel_pending(self, project_id: ProjectId, *, user_id: UserId | None = None) -> int:
        return self._cancel(
            lambda n: n.project_id == project_id and (user_id is None or n.user_id == user_id)
        )

    async def cancel_pending_moving_reminders(
        self, project_id: ProjectId, *, offsets: Iterable[MovingReminderOffset] | None = None
    ) -> int:
        wanted = None if offsets is None else set(offsets)
        return self._cancel(
            lambda n: (
                n.project_id == project_id
                and isinstance(n.target, MovingDateReminder)
                and (wanted is None or n.target.offset in wanted)
            )
        )

    async def cancel_pending_for_box(self, box_id: BoxId) -> int:
        return self._cancel(lambda n: _box_of(n) == box_id)

    def pending(self) -> list[Notification]:
        return [n for n in self.notifications if n.is_pending]

    def _cancel(self, predicate: Callable[[Notification], bool]) -> int:
        return sum(n.cancel() for n in self.notifications if predicate(n))


def _box_of(notification: Notification) -> BoxId | None:
    target = notification.target
    return target.box_id if isinstance(target, BoxContentsReminder) else None


class InMemoryAuditLogRepository(AbstractAuditLogRepository):
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def add(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def actions(self) -> list[AuditAction]:
        return [entry.action for entry in self.entries]


class InMemoryPushSubscriptionRepository(AbstractPushSubscriptionRepository):
    def __init__(self, subscriptions: Iterable[PushSubscription] = ()) -> None:
        self.subscriptions: dict[PushSubscriptionId, PushSubscription] = {
            s.id: replace(s) for s in subscriptions
        }

    async def add(self, subscription: PushSubscription) -> PushSubscription:
        existing = await self.get_by_endpoint(subscription.endpoint)
        if existing is not None:  # the race the real one resolves with ON CONFLICT
            existing.renew(user_id=subscription.user_id, keys=subscription.keys)
            subscription = existing
        self.subscriptions[subscription.id] = replace(subscription)
        return replace(subscription)

    async def get_by_endpoint(self, endpoint: PushEndpoint) -> PushSubscription | None:
        found = next((s for s in self.subscriptions.values() if s.endpoint == endpoint), None)
        return None if found is None else replace(found)

    async def list_active_for_user(self, user_id: UserId) -> list[PushSubscription]:
        return [
            replace(s) for s in self.subscriptions.values() if s.user_id == user_id and s.is_active
        ]

    async def update(self, subscription: PushSubscription) -> None:
        self.subscriptions[subscription.id] = replace(subscription)


class FakePushKeys(AbstractPushKeys):
    KEY = "BFakeApplicationServerKey"

    def public_key(self) -> str:
        return self.KEY


class FakePushSender(AbstractPushSender):
    """Answers with `results[endpoint]` (DELIVERED by default) and records what it sent."""

    def __init__(self) -> None:
        self.results: dict[str, PushResult] = {}
        self.sent: list[tuple[PushSubscription, PushMessage]] = []

    async def send(self, subscription: PushSubscription, message: PushMessage) -> PushResult:
        self.sent.append((subscription, message))
        return self.results.get(subscription.endpoint.value, PushResult(PushOutcome.DELIVERED))


class FakeNotificationContextReader(AbstractNotificationContextReader):
    """Returns `context` for every notification; tests set it to the facts they need."""

    def __init__(self, context: NotificationContext) -> None:
        self.context = context

    async def get(self, notification: Notification) -> NotificationContext:  # noqa: ARG002
        return self.context


class FakeInventoryReadModel(AbstractInventoryReadModel):
    """Views built from the in-memory repositories. Lists ignore filters and paging:
    their SQL is what matters, and it is tested against PostgreSQL."""

    TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)

    def __init__(
        self,
        users: InMemoryUserRepository,
        rooms: InMemoryRoomRepository,
        boxes: InMemoryBoxRepository,
        items: InMemoryItemRepository,
        notifications: InMemoryNotificationRepository,
    ) -> None:
        self._users = users
        self._rooms = rooms
        self._boxes = boxes
        self._items = items
        self._notifications = notifications
        self.searches: list[tuple[str, ...]] = []

    async def box(self, box_id: BoxId, *, viewer_id: UserId) -> BoxDTO | None:
        box = self._boxes.boxes.get(box_id)
        if box is None or not box.is_active:
            return None
        inside = self._in_box(box_id)
        reminders = (
            n.scheduled_at
            for n in self._notifications.pending()
            if _box_of(n) == box_id and n.user_id == viewer_id
        )
        creator = self._users.users[box.created_by]
        return BoxDTO(
            id=box.id,
            number=box.number.value,
            title=None if box.title is None else box.title.value,
            is_fragile=box.is_fragile,
            destination_room=self._room_ref(box.destination_room_id),
            status=box.status,
            status_changed_at=box.status_changed_at,
            created_by=InventoryUserRefDTO(id=creator.id, username=creator.username.value),
            qr_token=box.qr_token.value,
            item_count=len(inside),
            items_preview=tuple(item.name.value for item in inside[:3]),
            contents_reminder_at=next(reminders, None),
            created_at=self.TIMESTAMP,
            updated_at=self.TIMESTAMP,
        )

    async def boxes(
        self,
        project_id: ProjectId,
        *,
        viewer_id: UserId,
        statuses: Sequence[BoxStatus],  # noqa: ARG002
        destination_room_id: RoomId | None,  # noqa: ARG002
        number: int | None,  # noqa: ARG002
        page: CursorPageRequest,  # noqa: ARG002
    ) -> CursorPage[BoxDTO]:
        found = sorted(
            (b for b in self._boxes.boxes.values() if b.is_active and b.project_id == project_id),
            key=lambda b: b.number,
        )
        views = [await self.box(b.id, viewer_id=viewer_id) for b in found]
        return CursorPage(items=tuple(v for v in views if v is not None), next_cursor=None)

    async def qr_box(self, box_id: BoxId) -> QrBoxDTO | None:
        box = self._boxes.boxes.get(box_id)
        if box is None or not box.is_active:
            return None
        return QrBoxDTO(
            id=box.id,
            number=box.number.value,
            title=None if box.title is None else box.title.value,
            is_fragile=box.is_fragile,
            destination_room=self._room_ref(box.destination_room_id),
            status=box.status,
            items=tuple(
                QrBoxItemDTO(id=i.id, name=i.name.value, quantity=i.quantity.value)
                for i in self._in_box(box_id)
            ),
        )

    async def items(
        self,
        project_id: ProjectId,
        *,
        states: Sequence[ItemState],  # noqa: ARG002
        priority: Priority | None,  # noqa: ARG002
        box_id: BoxId | None,  # noqa: ARG002
        source_room_id: RoomId | None,  # noqa: ARG002
        page: CursorPageRequest,  # noqa: ARG002
    ) -> CursorPage[ItemDTO]:
        found = [
            i.id for i in self._items.items.values() if i.is_active and i.project_id == project_id
        ]
        return CursorPage(items=tuple(await self.items_by_ids(found)), next_cursor=None)

    async def items_by_ids(self, item_ids: Sequence[ItemId]) -> list[ItemDTO]:
        stored = [self._items.items.get(item_id) for item_id in item_ids]
        return [self._item_view(item) for item in stored if item is not None and item.is_active]

    async def search(
        self,
        project_id: ProjectId,  # noqa: ARG002
        words: Sequence[str],
        *,
        limit: int,  # noqa: ARG002
        offset: int,  # noqa: ARG002
    ) -> ItemSearchDTO:
        self.searches.append(tuple(words))
        return ItemSearchDTO(items=(), has_more=False)

    async def suggestions(
        self,
        project_id: ProjectId,  # noqa: ARG002
        source_room_id: RoomId,  # noqa: ARG002
        *,
        exclude_box_id: BoxId | None,  # noqa: ARG002
    ) -> list[BoxSuggestionDTO]:
        return []

    def _item_view(self, item: Item) -> ItemDTO:
        box = None if item.box_id is None else self._boxes.boxes[item.box_id]
        box_ref = (
            None
            if box is None
            else BoxRefDTO(
                id=box.id,
                number=box.number.value,
                status=box.status,
                destination_room=self._room_ref(box.destination_room_id),
            )
        )
        return ItemDTO(
            id=item.id,
            name=item.name.value,
            quantity=item.quantity.value,
            priority=item.priority,
            state=item.state,
            source_room=self._room_ref(item.source_room_id),
            box=box_ref,
            created_at=self.TIMESTAMP,
            updated_at=self.TIMESTAMP,
        )

    def _in_box(self, box_id: BoxId) -> list[Item]:
        inside = [i for i in self._items.items.values() if i.box_id == box_id and i.is_active]
        return sorted(inside, key=lambda i: (i.priority, i.name.value.lower(), i.id))

    def _room_ref(self, room_id: RoomId) -> RoomRefDTO:
        room = self._rooms.rooms[room_id]
        return RoomRefDTO(id=room.id, name=room.name.value)


def empty_summary() -> ProjectSummaryDTO:
    return ProjectSummaryDTO(
        boxes_by_status=dict.fromkeys(BoxStatus, 0),
        items_by_state=dict.fromkeys(ItemState, 0),
        tasks_by_phase=dict.fromkeys(TaskPhase, DoneCountDTO(total=0, done=0)),
        first_necessity=DoneCountDTO(total=0, done=0),
        next_task=None,
    )


class FakeSummaryReadModel(AbstractSummaryReadModel):
    """Answers with what a test put in and records every query. The aggregation
    itself is SQL: tests/integration/repositories/test_summary_reader.py."""

    def __init__(self) -> None:
        self.summary = empty_summary()
        self.checklist: list[LoadingChecklistRoomDTO] = []
        self.progress: list[UnpackingProgressRoomDTO] = []
        self.labels: list[BoxLabelDTO] = []
        self.queries: list[tuple[str, ProjectId]] = []
        self.label_box_ids: list[frozenset[BoxId] | None] = []

    async def project_summary(self, project_id: ProjectId) -> ProjectSummaryDTO:
        self.queries.append(("project_summary", project_id))
        return self.summary

    async def loading_checklist(self, project_id: ProjectId) -> list[LoadingChecklistRoomDTO]:
        self.queries.append(("loading_checklist", project_id))
        return list(self.checklist)

    async def unpacking_progress(self, project_id: ProjectId) -> list[UnpackingProgressRoomDTO]:
        self.queries.append(("unpacking_progress", project_id))
        return list(self.progress)

    async def box_labels(
        self, project_id: ProjectId, box_ids: Collection[BoxId] | None = None
    ) -> list[BoxLabelDTO]:
        self.queries.append(("box_labels", project_id))
        self.label_box_ids.append(None if box_ids is None else frozenset(box_ids))
        return list(self.labels)
