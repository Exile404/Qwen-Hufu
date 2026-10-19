"""The student's half of the tally: approving or rejecting what the agent filed (T2).

Each decision that touches the chain goes through student.act: signed with the owner key, or, once the
student has registered a passkey, with that passkey (the call then needs `signed`; see student.py).
"""

from datetime import UTC, datetime
from typing import Any

from eth_utils import to_checksum_address

from hufu import compliance, student
from hufu.chain import TxResult, to_units
from hufu.tools import Context


def _day_start(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())


def describe(a: dict[str, Any]) -> str:
    p = a["payload"]
    if a["kind"] == "payment":
        return f"Pay {p['amount']} {p['token']} to {p.get('payee') or p['to']}"
    if a["kind"] == "mandate":
        return f"{p['amount']} {p['token']} to {p.get('payee') or p['to']} every {p['every_days']} days"
    return f"Add payee {p['label']} ({p['address']})"


def _approve_call(ctx: Context, a: dict[str, Any], signed: dict[str, Any] | None) -> TxResult:
    p, chain = a["payload"], ctx.chain
    action, summary = f"approve:{a['id']}", "Approve: " + describe(a)
    if a["kind"] == "payment":
        return student.act(ctx, action, summary, "approveRequest", a["onchain_id"], signed=signed)
    if a["kind"] == "payee":
        address = to_checksum_address(p["address"])
        return student.act(ctx, action, summary, "setPayee", address, True, p["label"], signed=signed)
    if a["kind"] == "mandate":
        now = chain.now()
        first_due = now if p["first_due"] == "now" else max(now, _day_start(p["first_due"]))
        end = _day_start(p["end_date"]) + 86_399 if p["end_date"] else 0
        args = (
            to_checksum_address(p["to"]),
            chain.token(p["token"]).address,
            to_units(p["amount"]),
            int(p["every_days"]) * 86_400,
            first_due,
            end,
            p["label"],
        )
        return student.act(ctx, action, summary, "createMandate", *args, signed=signed)
    raise ValueError(f"unknown approval kind {a['kind']!r}")


def approve(ctx: Context, approval_id: int, signed: dict[str, Any] | None = None) -> dict[str, Any]:
    a = ctx.store.approval(approval_id)
    if not a:
        raise ValueError(f"no approval #{approval_id}")
    if a["status"] != "pending":
        raise ValueError(f"approval #{approval_id} is already {a['status']}")
    p = a["payload"]
    target = p.get("to") or p.get("address")
    if target and (hit := compliance.screen(target)):  # the list may have changed since it was filed
        reason = compliance.hit_reason(hit)
        ctx.store.decide(approval_id, "failed", None, reason)
        return {"approval_id": approval_id, "kind": a["kind"], "status": "blocked", "error": reason}

    res = _approve_call(ctx, a, signed)
    if res.error and res.error.startswith(student.RETRYABLE):  # a stale signature: the student can sign again
        return {"approval_id": approval_id, "kind": a["kind"], **res.as_dict()}
    ctx.store.decide(approval_id, "approved" if res.ok else "failed", res.tx_hash, res.error)
    out = {"approval_id": approval_id, "kind": a["kind"], **res.as_dict()}
    if res.ok and a["kind"] == "payment":
        rec = compliance.record_transfer(
            ctx,
            kind="approved request",
            to=p["to"],
            beneficiary=p.get("payee") or p["to"],
            token=p["token"],
            amount=float(p["amount"]),
            purpose=p.get("memo") or "",
            tx_hash=res.tx_hash,
        )
        if rec:
            out["transfer_record"] = rec
    return out


def reject(ctx: Context, approval_id: int, signed: dict[str, Any] | None = None) -> dict[str, Any]:
    a = ctx.store.approval(approval_id)
    if not a:
        raise ValueError(f"no approval #{approval_id}")
    if a["status"] != "pending":
        raise ValueError(f"approval #{approval_id} is already {a['status']}")
    tx_hash = None
    if a["kind"] == "payment":  # the request lives on-chain, so close it there too
        res = student.act(
            ctx,
            f"reject:{approval_id}",
            "Reject: " + describe(a),
            "rejectRequest",
            a["onchain_id"],
            signed=signed,
        )
        tx_hash = res.tx_hash
    ctx.store.decide(approval_id, "rejected", tx_hash)
    return {"approval_id": approval_id, "kind": a["kind"], "status": "rejected", "tx_hash": tx_hash}
