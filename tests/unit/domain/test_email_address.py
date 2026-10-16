import pytest
from email_validator import EmailNotValidError, validate_email

from app.domain.users.email_address import (
    EMAIL_MAX_LENGTH,
    LOCAL_PART_MAX_LENGTH,
    SPECIAL_USE_DOMAINS,
    normalize_email,
)
from app.domain.users.errors import InvalidEmailError

VALID = [
    "john@example.com",
    "John.Doe@Example.COM",
    "  john@example.com\t",
    "a@b.co",
    "a@x.c",
    "a@x.c0m",
    "first.middle.last@sub.domain.example.com",
    "a+tag@example.com",
    "a!#$%&'*+/=?^_`{|}~-@example.com",
    "1234567890@example.com",
    "a@xn--e1afmkfd.xn--p1ai",
    "иван@пример.рф",
    "Иван@Пример.РФ",
    "üñî@example.com",
    "test@ëxample.com",
    "a@bücher.de",
    "a@my-host.example.com",
    "a" * LOCAL_PART_MAX_LENGTH + "@example.com",
    "a@" + "b" * 63 + ".com",
]
INVALID = [
    "",
    "john",
    "john@",
    "@example.com",
    "john@example",
    "a@localhost",
    "jo hn@example.com",
    "a..b@example.com",
    ".a@example.com",
    "a.@example.com",
    '"quoted"@example.com',
    "a\\b@example.com",
    "a(b)@example.com",
    "a@b@example.com",
    "a@[1.2.3.4]",
    "a@1.2.3.4",
    "a@example.123",
    "a@-example.com",
    "a@example-.com",
    "a@exa_mple.com",
    "a@exa mple.com",
    "a@example..com",
    "a@.example.com",
    "a@example.com.",
    "a@example.com\nx",
    "a\u200b@example.com",  # zero-width space
    "a\u0000@example.com",
    "a@" + "b" * 64 + ".com",
    *(f"a@mail.{name}" for name in sorted(SPECIAL_USE_DOMAINS)),
]


@pytest.mark.parametrize("address", VALID)
def test_accepts(address: str) -> None:
    normalize_email(address)


@pytest.mark.parametrize("address", INVALID)
def test_rejects(address: str) -> None:
    with pytest.raises(InvalidEmailError):
        normalize_email(address)


@pytest.mark.parametrize(
    ("address", "normalized"),
    [
        ("  John.Doe@Example.COM ", "john.doe@example.com"),
        ("Иван@Пример.РФ", "иван@пример.рф"),
        ("ivan@xn--e1afmkfd.xn--p1ai", "ivan@пример.рф"),  # punycode and Unicode: one address
        ("é@example.com", "é@example.com"),  # NFC: decomposed é is composed
    ],
)
def test_normalizes_to_one_canonical_form(address: str, normalized: str) -> None:
    assert normalize_email(address) == normalized


def test_local_part_is_limited_in_bytes_not_characters() -> None:
    """RFC 5321 counts octets: 33 two-byte letters are 66 bytes, too many."""
    with pytest.raises(InvalidEmailError):
        normalize_email("ж" * 33 + "@example.com")
    normalize_email("ж" * 32 + "@example.com")


def test_whole_address_is_limited() -> None:
    domain = ".".join(["b" * 63] * 3) + ".com"  # 195 characters
    longest = "a" * (EMAIL_MAX_LENGTH - len(domain) - 1)

    assert normalize_email(f"{longest}@{domain}") == f"{longest}@{domain}"
    with pytest.raises(InvalidEmailError):
        normalize_email(f"{longest}a@{domain}")


def _reference(address: str) -> str | None:
    """What pydantic's EmailStr (email-validator, no DNS lookups) makes of it."""
    try:
        result = validate_email(address.strip(), check_deliverability=False)
    except EmailNotValidError:
        return None
    return result.normalized.lower()


@pytest.mark.parametrize("address", VALID + INVALID)
def test_agrees_with_email_validator(address: str) -> None:
    """Same verdict and same canonical form as the library v0 relies on."""
    try:
        ours: str | None = normalize_email(address)
    except InvalidEmailError:
        ours = None

    assert ours == _reference(address)


def test_is_stricter_than_email_validator_only_on_the_local_part_length() -> None:
    too_long = "a" * (LOCAL_PART_MAX_LENGTH + 1) + "@example.com"

    assert _reference(too_long) is not None
    with pytest.raises(InvalidEmailError):
        normalize_email(too_long)
