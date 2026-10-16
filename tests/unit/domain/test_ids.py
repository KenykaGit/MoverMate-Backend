import time

from app.domain.common.ids import new_id


def test_new_id_is_rfc_9562_version_7() -> None:
    value = new_id()

    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_ids_are_strictly_monotonic_within_a_millisecond() -> None:
    """Ordering by id must mean ordering by creation, even in a tight loop."""
    ids = [new_id() for _ in range(10_000)]

    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_ids_carry_the_creation_time() -> None:
    before_ms = time.time_ns() // 1_000_000
    value = new_id()
    after_ms = time.time_ns() // 1_000_000

    assert before_ms <= value.int >> 80 <= after_ms + 1
