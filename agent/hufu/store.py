"""Local state that does not belong on-chain: memo texts and the approval queue.

Only keccak256(memo) goes on-chain; the text stays here, on the student's device.
Rows are scoped to a vault address so a redeployed local chain starts clean.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS memos (
    hash TEXT PRIMARY KEY,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lab_emails (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    vault      TEXT NOT NULL,
    sender     TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vault       TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- payment | mandate | payee
    payload     TEXT NOT NULL,          -- JSON
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | failed
    onchain_id  INTEGER,                -- HufuVault request id, for kind = payment
    created_at  TEXT NOT NULL,
    decided_at  TEXT,
    tx_hash     TEXT,
    error       TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path, vault: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.vault = vault.lower()

    # --- memos
    def save_memo(self, memo_hash: str, text: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO memos (hash, text) VALUES (?, ?)", (memo_hash.lower(), text))
        self.db.commit()

    def memo(self, memo_hash: str) -> str | None:
        row = self.db.execute("SELECT text FROM memos WHERE hash = ?", (memo_hash.lower(),)).fetchone()
        return row["text"] if row else None

    # --- attack-lab emails (added from the dashboard, shown after the seeded inbox)
    def add_lab_email(self, sender: str, subject: str, body: str) -> int:
        cur = self.db.execute(
            "INSERT INTO lab_emails (vault, sender, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (self.vault, sender, subject, body, _now()),
        )
        self.db.commit()
        assert cur.lastrowid is not None
        return 100 + cur.lastrowid

    def lab_emails(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, sender, subject, body, created_at FROM lab_emails WHERE vault = ? ORDER BY id",
            (self.vault,),
        ).fetchall()
        return [
            {
                "id": 100 + r["id"],
                "from": r["sender"],
                "subject": r["subject"],
                "date": r["created_at"][:10],
                "body": r["body"],
                "lab": True,
            }
            for r in rows
        ]

    # --- approvals
    def add_approval(
        self, kind: str, payload: dict[str, Any], reason: str = "", onchain_id: int | None = None
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO approvals (vault, kind, payload, reason, onchain_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.vault, kind, json.dumps(payload), reason, onchain_id, _now()),
        )
        self.db.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def approval(self, approval_id: int) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT * FROM approvals WHERE id = ? AND vault = ?", (approval_id, self.vault)
        ).fetchone()
        return _row(row) if row else None

    def approvals(self, status: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM approvals WHERE vault = ?", [self.vault]
        if status:
            q += " AND status = ?"
            args.append(status)
        return [_row(r) for r in self.db.execute(q + " ORDER BY id", args).fetchall()]

    def decide(
        self, approval_id: int, status: str, tx_hash: str | None = None, error: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, tx_hash = ?, error = ? WHERE id = ? AND vault = ?",
            (status, _now(), tx_hash, error, approval_id, self.vault),
        )
        self.db.commit()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    return d
