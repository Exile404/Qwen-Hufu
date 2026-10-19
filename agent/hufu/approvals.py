"""The student's half of the tally: approving or rejecting what the agent filed (T2).

These run with the owner key. In the demo that key lives in .env; in production it would be
a passkey on the student's phone, and these functions would only build the transaction.
"""

from datetime import UTC, datetime
from typing import Any

from hufu.chain import to_units
from hufu.tools import Context


def _day_start(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def approve(ctx: Context, approval_id: int) -> dict[str, Any]:
    a = ctx.store.approval(approval_id)
    if not a:
        raise ValueError(f"no approval #{approval_id}")
    if a["status"] != "pending":
        raise ValueError(f"approval #{approval_id} is already {a['status']}")
    p, chain = a["payload"], ctx.chain

    if a["kind"] == "payment":
        res = chain.approve_request(a["onchain_id"])
    elif a["kind"] == "payee":
        res = chain.set_payee(p["address"], True, p["label"])
    elif a["kind"] == "mandate":
        now = chain.now()
        first_due = now if p["first_due"] == "now" else max(now, _day_start(p["first_due"]))
        end = _day_start(p["end_date"]) + 86_399 if p["end_date"] else 0
        res = chain.create_mandate(
            p["to"],
            p["token"],
            to_units(p["amount"]),
            int(p["every_days"]) * 86_400,
            first_due,
            end,
            p["label"],
        )
    else:
        raise ValueError(f"unknown approval kind {a['kind']!r}")

    ctx.store.decide(approval_id, "approved" if res.ok else "failed", res.tx_hash, res.error)
    return {"approval_id": approval_id, "kind": a["kind"], **res.as_dict()}


def reject(ctx: Context, approval_id: int) -> dict[str, Any]:
    a = ctx.store.approval(approval_id)
    if not a:
        raise ValueError(f"no approval #{approval_id}")
    if a["status"] != "pending":
        raise ValueError(f"approval #{approval_id} is already {a['status']}")
    tx_hash = None
    if a["kind"] == "payment":  # the request lives on-chain, so close it there too
        res = ctx.chain.reject_request(a["onchain_id"])
        tx_hash = res.tx_hash
    ctx.store.decide(approval_id, "rejected", tx_hash)
    return {"approval_id": approval_id, "kind": a["kind"], "status": "rejected", "tx_hash": tx_hash}
