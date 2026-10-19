"""Provenance: did a value come from the student, or from a third party?

The student's own messages are trusted. Tool outputs marked untrusted (emails, web pages) are not.
A payee address or amount that appears *only* in untrusted text is tainted: it cannot authorise a
payment by itself, however convincing the text around it is.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from hufu.guard.scanner import ScanResult

_NUM_RE = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")
_STRIP_RE = re.compile(r"0x[0-9a-fA-F]{8,}|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}:\d{2}\b")


def numbers_in(text: str) -> set[Decimal]:
    """Numbers written in text: '4,500', '4500.0' and 'HKD 9,000' all count. Ignores hex and dates."""
    out = set()
    for raw in _NUM_RE.findall(_STRIP_RE.sub(" ", text)):
        try:
            out.add(Decimal(raw.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return out


@dataclass
class UntrustedItem:
    source: str
    text: str
    scan: ScanResult


@dataclass
class Provenance:
    trusted: list[str] = field(default_factory=list)
    untrusted: list[UntrustedItem] = field(default_factory=list)

    def add_trusted(self, text: str) -> None:
        self.trusted.append(text)

    def add_untrusted(self, source: str, text: str, scan: ScanResult) -> None:
        self.untrusted.append(UntrustedItem(source, text, scan))

    def from_student(self, address: str) -> bool:
        a = address.lower()
        return any(a in t.lower() for t in self.trusted)

    def third_party_sources(self, address: str) -> list[UntrustedItem]:
        a = address.lower()
        return [u for u in self.untrusted if a in u.text.lower()]

    def amount_only_from_third_party(self, amount: object) -> bool:
        try:
            n = Decimal(str(amount)).normalize()
        except InvalidOperation:
            return False
        said_by_student = any(n in numbers_in(t) for t in self.trusted)
        seen_elsewhere = any(n in numbers_in(u.text) for u in self.untrusted)
        return seen_elsewhere and not said_by_student
