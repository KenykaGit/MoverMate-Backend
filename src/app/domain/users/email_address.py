"""Email address syntax (RFC 5321/5322 dot-atom, RFC 6531 internationalized addresses).

The domain layer uses the standard library only, so this is a small
implementation of the syntax rules that `email-validator` (pydantic's EmailStr)
applies with deliverability checks off; tests/unit/domain/test_email_address.py
checks both agree. Not supported, as there: quoted local parts, IP literals.
Stricter than it: the local part is limited to 64 octets (RFC 5321 §4.5.3.1.1).
"""

import re
import string
import unicodedata

from app.domain.users.errors import InvalidEmailError

EMAIL_MAX_LENGTH = 254  # RFC 5321 path limit minus the angle brackets
LOCAL_PART_MAX_LENGTH = 64
DOMAIN_MAX_LENGTH = 253

# RFC 5322 atext; dots separate atoms.
_ATEXT = frozenset(string.ascii_letters + string.digits + "!#$%&'*+/=?^_`{|}~-")
# Invisible, control, private-use and unassigned characters: never part of an address.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zs", "Zl", "Zp"})
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# RFC 6761 and RFC 7686 names that never receive email (as in email-validator).
SPECIAL_USE_DOMAINS = frozenset({"arpa", "invalid", "local", "localhost", "onion", "test"})


def normalize_email(raw: str) -> str:
    """The address in canonical form: trimmed, NFC, lowercase, domain in Unicode.

    `John@Пример.РФ`, `john@пример.рф` and `john@xn--e1afmkfd.xn--p1ai` are one
    address, so they normalize to the same string.

    Raises:
        InvalidEmailError: the address is not syntactically valid.
    """
    address = unicodedata.normalize("NFC", raw.strip()).lower()
    local, separator, domain = address.rpartition("@")
    if not separator or not local:
        raise InvalidEmailError("Email address must have the form name@domain")
    _check_local_part(local)
    ascii_domain = _ascii_domain(domain)
    if len(local.encode()) + 1 + len(ascii_domain) > EMAIL_MAX_LENGTH:
        raise InvalidEmailError(f"Email must be at most {EMAIL_MAX_LENGTH} characters long")
    return f"{local}@{ascii_domain.encode('ascii').decode('idna')}"


def _check_local_part(local: str) -> None:
    if len(local.encode()) > LOCAL_PART_MAX_LENGTH:
        raise InvalidEmailError(
            f"The part before the @ must be at most {LOCAL_PART_MAX_LENGTH} bytes long"
        )
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise InvalidEmailError("The part before the @ must not start, end or repeat with a dot")
    for char in local:
        if char == "." or char in _ATEXT:
            continue
        if char.isascii() or unicodedata.category(char) in _UNSAFE_CATEGORIES:
            raise InvalidEmailError("The part before the @ contains an invalid character")


def _ascii_domain(domain: str) -> str:
    """The domain as ASCII (IDNA), validated label by label."""
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as error:  # empty or overlong label, invalid punycode
        raise InvalidEmailError("The domain of the email address is invalid") from error
    labels = ascii_domain.split(".")
    if len(labels) < 2:  # noqa: PLR2004  # a name and a top-level domain
        raise InvalidEmailError("The domain of the email address must contain a dot")
    if len(ascii_domain) > DOMAIN_MAX_LENGTH or not all(
        _DOMAIN_LABEL.fullmatch(label) for label in labels
    ):
        raise InvalidEmailError("The domain of the email address is invalid")
    if labels[-1].isdigit():
        raise InvalidEmailError("The domain of the email address has no valid top-level domain")
    if labels[-1] in SPECIAL_USE_DOMAINS:
        raise InvalidEmailError("The domain of the email address is reserved and cannot get mail")
    return ascii_domain
