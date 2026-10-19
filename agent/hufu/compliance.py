"""Compliance controls next to the guard. docs/COMPLIANCE.md maps each one to the rule behind it.

  screen(address)    counterparty screening against the project's list plus the Ethereum addresses on
                     the U.S. Treasury OFAC SDN list (`make sanctions-update`). A hit blocks paying,
                     filing a request for, or saving that address, even when the student typed it.
  record_transfer()  a travel-rule-ready record of every transfer of HK$8,000 or more: originator,
                     beneficiary, amount, purpose and transaction, kept on the device, never on-chain.

Hufu is software in the student's hands, not a virtual-asset service provider or a stablecoin issuer:
identity checks (KYC) happen at the licensed issuer or on-ramp. These controls make sure the wallet
never pays a listed counterparty and can answer a counterparty institution's travel-rule request.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import yaml
from eth_utils import to_checksum_address

from hufu.config import REPO_ROOT, get_settings

DIR = REPO_ROOT / "agent" / "data" / "compliance"
LIST_FILE = DIR / "screening.yaml"
OFAC_FILE = DIR / "ofac_eth.txt"
RECORD_THRESHOLD_HKD = 8_000  # Hong Kong's travel-rule threshold for the fuller data set

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    vault          TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    kind           TEXT NOT NULL,      -- payment | recurring payment | approved request
    tx_hash        TEXT,
    token          TEXT NOT NULL,
    amount         REAL NOT NULL,
    hkd_equivalent REAL NOT NULL,
    originator     TEXT NOT NULL,      -- JSON {name, wallet}
    beneficiary    TEXT NOT NULL,      -- JSON {name, wallet}
    purpose        TEXT NOT NULL
);
"""


@dataclass
class Hit:
    address: str
    list: str
    reason: str


_cache: dict[str, Any] = {"key": None, "entries": {}, "meta": {}}


def _load() -> tuple[dict[str, Hit], dict[str, Any]]:
    """The combined screening list, reloaded whenever either file changes."""
    key = tuple((p, p.stat().st_mtime if p.exists() else 0.0) for p in (LIST_FILE, OFAC_FILE))
    if _cache["key"] == key:
        return _cache["entries"], _cache["meta"]
    entries: dict[str, Hit] = {}
    meta: dict[str, Any] = {"list_updated": None, "ofac_entries": 0, "ofac_fetched": None}
    if LIST_FILE.exists():
        spec = yaml.safe_load(LIST_FILE.read_text()) or {}
        meta["list_updated"] = str(spec.get("updated") or "")
        for e in spec.get("entries") or []:
            addr = to_checksum_address(str(e["address"]))
            entries[addr.lower()] = Hit(addr, str(e.get("list") or "Hufu list"), str(e.get("reason") or ""))
    if OFAC_FILE.exists():
        for line in OFAC_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("# fetched:"):
                meta["ofac_fetched"] = line.split(":", 1)[1].strip()
            elif re.fullmatch(r"0x[0-9a-fA-F]{40}", line):
                meta["ofac_entries"] += 1
                entries.setdefault(
                    line.lower(),
                    Hit(to_checksum_address(line), "OFAC SDN", "on the U.S. Treasury sanctions list"),
                )
    _cache.update(key=key, entries=entries, meta=meta)
    return entries, meta


def screen(address: str) -> Hit | None:
    return _load()[0].get(str(address).strip().lower())


def screening_status() -> dict[str, Any]:
    entries, meta = _load()
    return {"entries": len(entries), **meta}


def hit_reason(hit: Hit) -> str:
    return (
        f"{hit.address} is on a screening list ({hit.list}: {hit.reason}). "
        "Hufu will not pay it, file a payment to it or save it, even on request"
    )


# ---------------------------------------------------------------- travel-rule-ready records


def hkd_equivalent(ctx: Any, token: str, amount: float) -> float:
    if token == "tHKD":
        return round(float(amount), 2)
    rate = ctx.chain.pool.functions.hkdPerUsd().call() / 1e6
    return round(float(amount) * rate, 2)


def record_transfer(
    ctx: Any,
    *,
    kind: str,
    to: str,
    beneficiary: str,
    token: str,
    amount: float,
    purpose: str,
    tx_hash: str | None,
) -> int | None:
    """Keep a record of a transfer of HK$8,000 or more. Returns the record id, or None if below."""
    hkd = hkd_equivalent(ctx, token, amount)
    if hkd < RECORD_THRESHOLD_HKD:
        return None
    db = ctx.store.db
    db.executescript(SCHEMA)
    originator = {"name": get_settings().account_holder, "wallet": ctx.chain.vault.address}
    cur = db.execute(
        "INSERT INTO transfer_records (vault, created_at, kind, tx_hash, token, amount, hkd_equivalent, "
        "originator, beneficiary, purpose) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ctx.store.vault,
            datetime.now(UTC).isoformat(timespec="seconds"),
            kind,
            tx_hash,
            token,
            float(amount),
            hkd,
            json.dumps(originator),
            json.dumps({"name": beneficiary, "wallet": to_checksum_address(to)}),
            purpose or "",
        ),
    )
    db.commit()
    return cur.lastrowid


def records(ctx: Any, limit: int = 20) -> list[dict[str, Any]]:
    db = ctx.store.db
    db.executescript(SCHEMA)
    rows = db.execute(
        "SELECT * FROM transfer_records WHERE vault = ? ORDER BY id DESC LIMIT ?", (ctx.store.vault, limit)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["originator"], d["beneficiary"] = json.loads(d["originator"]), json.loads(d["beneficiary"])
        out.append(d)
    return out
