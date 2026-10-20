"""Local state that does not belong on-chain: memo texts, the approval queue and autopilot alerts.

Only keccak256(memo) goes on-chain; the text stays here, on the student's device.
Rows are scoped to a vault address so a redeployed local chain starts clean.
"""

import json
import sqlite3
import threading
from collections.abc import Iterator
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
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vault       TEXT NOT NULL,
    level       TEXT NOT NULL,          -- action | warning | critical
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL,          -- JSON
    dedupe      TEXT,                   -- the same alert is raised only once
    created_at  TEXT NOT NULL,
    UNIQUE (vault, dedupe)
);
CREATE TABLE IF NOT EXISTS kv (
    vault TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL,                -- JSON
    PRIMARY KEY (vault, key)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Rows:
    """A statement's result, read in full while the lock is held, so no cursor outlives it."""

    def __init__(self, cur: sqlite3.Cursor):
        self.rowcount = cur.rowcount
        self.lastrowid = cur.lastrowid
        self._rows = cur.fetchall()

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._rows)


class SharedConnection:
    """One SQLite connection shared by the API's request threads and the autopilot thread.

    sqlite3 does not serialise concurrent use of one connection: a dashboard poll racing an autopilot run
    failed with "bad parameter or other API misuse", and that run never froze the wallet. Every call
    holds one lock; `lock` is re-entrant so a read-then-write (the audit chain's next hash) can hold it too."""

    def __init__(self, path: Path):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def execute(self, sql: str, params: Any = ()) -> Rows:
        with self.lock:
            return Rows(self._db.execute(sql, params))

    def executescript(self, sql: str) -> None:
        with self.lock:
            self._db.executescript(sql)

    def commit(self) -> None:
        with self.lock:
            self._db.commit()


class Store:
    def __init__(self, path: Path, vault: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = SharedConnection(path)
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

    # --- which deployment these rows belong to
    def bind_deployment(self, marker: str) -> bool:
        """Rows are keyed by vault address, but a fresh local chain redeploys the vault at the same
        address. When the deployment underneath changed, forget this vault's rows (approvals, alerts,
        audit, settings) so a new chain starts clean. Returns True if anything was forgotten."""
        old = self.get("deployment")
        if old == marker:
            return False
        if old is not None:
            tables = [r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            for table in tables:
                if "vault" in [c[1] for c in self.db.execute(f"PRAGMA table_info({table})")]:
                    self.db.execute(f"DELETE FROM {table} WHERE vault = ?", (self.vault,))
        self.put("deployment", marker)
        return old is not None

    # --- autopilot alerts and small settings
    def add_alert(
        self, level: str, kind: str, title: str, detail: dict[str, Any], dedupe: str | None = None
    ) -> bool:
        """Record an alert for the student. Returns False if one with the same `dedupe` key exists."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO alerts (vault, level, kind, title, detail, dedupe, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.vault, level, kind, title, json.dumps(detail, default=str), dedupe, _now()),
        )
        self.db.commit()
        return cur.rowcount == 1

    def alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT id, level, kind, title, detail, created_at FROM alerts WHERE vault = ? ORDER BY id DESC LIMIT ?",
            (self.vault, limit),
        ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute(
            "SELECT value FROM kv WHERE vault = ? AND key = ?", (self.vault, key)
        ).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv (vault, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT (vault, key) DO UPDATE SET value = excluded.value",
            (self.vault, key, json.dumps(value, default=str)),
        )
        self.db.commit()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    return d
