"""Demo seed data (fictional), rendered against the live deployment."""

import re
from pathlib import Path
from typing import Any

import yaml
from eth_utils import keccak, to_checksum_address

from hufu.chain import lookalike

SEED_DIR = Path(__file__).resolve().parents[1] / "data" / "seed"
ATTACKER = to_checksum_address("0x" + keccak(text="hufu.attacker").hex()[:40])
# Demo stand-in for a sanctioned address; it is on agent/data/compliance/screening.yaml.
SANCTIONED = to_checksum_address("0x" + keccak(text="hufu.sanctioned").hex()[:40])


def render(text: str, payees: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key == "attacker":
            return ATTACKER
        if key == "sanctioned":
            return SANCTIONED
        if key.startswith("lookalike:"):
            return lookalike(payees[key.split(":", 1)[1]])
        return payees[key]

    return re.sub(r"\{([a-z:]+)\}", sub, text)


def inbox(payees: dict[str, str]) -> list[dict[str, Any]]:
    items = yaml.safe_load((SEED_DIR / "inbox.yaml").read_text())
    return [{**m, "body": render(m["body"], payees).strip()} for m in items]
