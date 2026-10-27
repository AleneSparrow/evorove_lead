"""Keep real email addresses out of documentation and test fixtures."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("docs", "tests", "owner-observations")
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
ALLOWED_DOMAINS = frozenset({"example.com", "example.org", "evorove.com"})
ALLOWED_SUFFIXES = (".example", ".example.com", ".example.org")


def _looks_like_url_credentials(text: str, start: int) -> bool:
    """A URL user:password@host is not an email address."""
    return bool(re.search(r"https?://[^/\s]*:$", text[max(0, start - 200):start]))


def _unapproved_emails(text: str) -> tuple[str, ...]:
    rejected = []
    for match in EMAIL.finditer(text):
        local, domain = match.group(1).casefold(), match.group(2).casefold()
        if _looks_like_url_credentials(text, match.start()):
            continue
        if local == "noreply" or domain in ALLOWED_DOMAINS or domain.endswith(ALLOWED_SUFFIXES):
            continue
        rejected.append(match.group(0))
    return tuple(rejected)


def test_email_guard_rejects_nonreserved_domain() -> None:
    address = "person@" + "nonreserved.test"
    assert _unapproved_emails(address) == (address,)


def test_docs_observations_and_tests_use_only_reserved_email_domains() -> None:
    offenders = []
    for folder in SCANNED:
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_bytes().decode("utf-8", errors="ignore")
            for address in _unapproved_emails(text):
                offenders.append(f"{path.relative_to(ROOT)}: {address}")
    assert not offenders, "Replace nonreserved email addresses with example domains:\n" + "\n".join(offenders)
