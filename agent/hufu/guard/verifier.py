"""Output verifier: the agent's reply may only state facts that actually happened.

Every number must appear in the student's messages or a tool result, and every transaction hash
must be one a tool actually returned. Catches slips like "80 tHKD" for 800, or an invented hash.
"""

import re
from decimal import Decimal

from hufu.guard.provenance import numbers_in

TX_RE = re.compile(r"0x[0-9a-fA-F]{64}")
DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
SMALL = Decimal(10)  # list markers, ids and counts below this are not checked


def _grounded(n: Decimal, known: set[Decimal]) -> bool:
    if n in known:
        return True
    return any(abs(k - n) < Decimal("0.01") or k.quantize(Decimal(1)) == n for k in known)


def check_reply(reply: str, grounding: list[str]) -> list[str]:
    corpus = "\n".join(grounding)
    problems = []
    real_hashes = {h.lower() for h in TX_RE.findall(corpus)}
    for h in TX_RE.findall(reply):
        if h.lower() not in real_hashes:
            problems.append(f"transaction hash {h[:10]}… does not match any real transaction")
    known = numbers_in(corpus)
    for y, m, d in DATE_RE.findall(corpus):  # "2027-06-01" grounds "June 1, 2027"
        known |= {Decimal(y), Decimal(int(m)), Decimal(int(d))}
    for n in sorted(numbers_in(reply)):
        if n >= SMALL and not _grounded(n, known):
            problems.append(f"the number {n:,f} is not in any tool result or the student's message")
    return problems
