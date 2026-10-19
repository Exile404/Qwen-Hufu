"""Scam and prompt-injection scanner for third-party text (emails, web pages, memos).

Rule-based on purpose: fast, explainable in one line each, and it cannot itself be prompt-injected.
"""

import re
from dataclasses import dataclass, field

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")

# (kind, plain-language detail, weight, pattern)
RULES: list[tuple[str, str, int, re.Pattern[str]]] = [
    (
        "injection",
        "contains instructions aimed at AI assistants",
        5,
        re.compile(
            r"(note|message|instruction)s? to (ai|assistant|agent)|"
            r"\b(ai|assistant|agent)s?\b[^.\n]{0,40}\b(processing|reading) this|"
            r"ignore (all |any )?(previous|prior|earlier) instructions|system prompt",
            re.IGNORECASE,
        ),
    ),
    (
        "injection",
        "claims the payment is pre-approved or says not to confirm",
        5,
        re.compile(
            r"pre-?approved|do not ask (for )?confirmation|no need to (ask|confirm)|"
            r"without (asking|confirmation)|don'?t (ask|check)",
            re.IGNORECASE,
        ),
    ),
    (
        "payment_change",
        "asks to pay a new or changed account",
        4,
        re.compile(
            r"(account|wallet|bank details?|receiving address)[^.\n]{0,25}\b(changed|updated|has a problem|migrat)|"
            r"\bnew (account|wallet|address|bank details)\b|from now on[^.\n]{0,40}\b(send|pay)",
            re.IGNORECASE,
        ),
    ),
    (
        "urgency",
        "pressure: urgent deadline, penalty or account threat",
        2,
        re.compile(
            r"\burgent\b|within \d+ ?(hours|hrs|minutes)|\bimmediately\b|late (fee|penalty)|"
            r"enrol?ment hold|(suspend|clos|block)(ed|ure)",
            re.IGNORECASE,
        ),
    ),
]

HIGH, MEDIUM = 5, 3


def is_lookalike(a: str, b: str) -> bool:
    """Same first 4 and last 4 hex characters, but a different address: classic address poisoning."""
    a, b = a.lower(), b.lower()
    return a != b and a[2:6] == b[2:6] and a[-4:] == b[-4:]


def lookalike_warning(fake: str, real: str, name: str) -> str:
    """Spell out the difference: shortened addresses are exactly what makes poisoning work on people."""
    return (
        f"{fake} is NOT the saved {name} address {real}: same first and last characters, "
        f"different middle ({fake[6:14]}… vs {real[6:14]}…). Likely address poisoning"
    )


@dataclass
class Finding:
    kind: str
    detail: str
    weight: int


@dataclass
class ScanResult:
    risk: str  # high | medium | low
    score: int
    findings: list[Finding] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"risk": self.risk, "findings": [f.detail for f in self.findings]}


def scan(text: str, saved_payees: dict[str, str]) -> ScanResult:
    """`saved_payees` maps name -> address for the student's allowlist."""
    findings = [Finding(kind, detail, w) for kind, detail, w, rx in RULES if rx.search(text)]
    addresses = sorted(set(ADDRESS_RE.findall(text)))
    saved = {a.lower(): (n, a) for n, a in saved_payees.items()}
    for addr in addresses:
        if addr.lower() in saved:
            continue
        twin = next((v for a, v in saved.items() if is_lookalike(addr, a)), None)
        if twin:
            findings.append(Finding("address_poisoning", lookalike_warning(addr, twin[1], twin[0]), 6))
        else:
            findings.append(Finding("unknown_address", f"{addr[:8]}…{addr[-4:]} is not a saved payee", 2))
    score = sum(f.weight for f in findings)
    risk = "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"
    return ScanResult(risk, score, findings, addresses)
