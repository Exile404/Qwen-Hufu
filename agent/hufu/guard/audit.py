"""Hash-chained audit log. Each entry commits to the previous one, so editing or deleting any
past entry breaks every hash after it. The head is periodically anchored on-chain
(HufuVault.anchorAudit), which timestamps it and makes silent rewrites detectable.
"""

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from eth_utils import keccak

GENESIS = "0x" + "00" * 32

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    vault     TEXT NOT NULL,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    data      TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash      TEXT NOT NULL
);
"""


def _entry_hash(prev: str, ts: str, kind: str, data: str) -> str:
    body = json.dumps({"prev": prev, "ts": ts, "kind": kind, "data": data}, separators=(",", ":"))
    return "0x" + keccak(text=body).hex()


class AuditLog:
    def __init__(self, db: sqlite3.Connection, vault: str):
        self.db = db
        self.vault = vault.lower()
        self.db.executescript(SCHEMA)

    def head(self) -> str:
        row = self.db.execute(
            "SELECT hash FROM audit WHERE vault = ? ORDER BY id DESC LIMIT 1", (self.vault,)
        ).fetchone()
        return row[0] if row else GENESIS

    def append(self, kind: str, data: dict[str, Any]) -> str:
        prev = self.head()
        ts = datetime.now(UTC).isoformat(timespec="milliseconds")
        payload = json.dumps(data, default=str, ensure_ascii=False, sort_keys=True)
        h = _entry_hash(prev, ts, kind, payload)
        self.db.execute(
            "INSERT INTO audit (vault, ts, kind, data, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?)",
            (self.vault, ts, kind, payload, prev, h),
        )
        self.db.commit()
        return h

    def recent(self, n: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, ts, kind, data, hash FROM audit WHERE vault = ? ORDER BY id DESC LIMIT ?",
            (self.vault, n),
        ).fetchall()
        return [{"id": r[0], "ts": r[1], "kind": r[2], "data": json.loads(r[3]), "hash": r[4]} for r in rows]

    def verify(self) -> dict[str, Any]:
        """Recompute the whole chain. Returns the first broken entry, if any."""
        prev = GENESIS
        count = 0
        for id_, ts, kind, data, prev_hash, h in self.db.execute(
            "SELECT id, ts, kind, data, prev_hash, hash FROM audit WHERE vault = ? ORDER BY id", (self.vault,)
        ):
            if prev_hash != prev or _entry_hash(prev, ts, kind, data) != h:
                return {"ok": False, "entries": count, "broken_at": id_}
            prev, count = h, count + 1
        return {"ok": True, "entries": count, "head": prev}

    def contains(self, h: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM audit WHERE vault = ? AND hash = ?", (self.vault, h.lower())
            ).fetchone()
            is not None
        )
