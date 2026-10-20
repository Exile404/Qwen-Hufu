"""The agent's tools. Every tool has a tier:

  T0  read only, no key involved
  T1  acts alone with the agent key, inside the limits the vault enforces
  T2  only *files* a request; the student's key executes it (see approvals.py)

There is no T3 tool: changing limits, payees or keys has no code path from the agent.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from eth_utils import is_address, to_checksum_address

from hufu import compliance, huitong, planner, seed
from hufu.chain import Chain, memo_hash, to_units
from hufu.store import Store

MEMO_MAX = 80
CAP_ERRORS = ("PayeeNotAllowed", "OverPerTxCap", "OverPayeeCap", "OverDailyCap")


@dataclass
class Context:
    chain: Chain
    store: Store
    # Passkey challenges issued to the dashboard and not yet signed (see student.py).
    passkey_pending: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    tier: str
    description: str
    handler: Callable[[Context, dict[str, Any]], dict[str, Any]]
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    untrusted_output: bool = False  # output may contain text written by third parties

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.properties, "required": self.required},
            },
        }


TOKEN = {"type": "string", "enum": ["tHKD", "tUSD"], "description": "tHKD (HK dollar) or tUSD (US dollar)"}
AMOUNT = {"type": "number", "description": "Amount in token units, e.g. 4500"}
PAYEE = {"type": "string", "description": "Saved payee name: landlord, university, roommate or phone"}
MEMO = {
    "type": "string",
    "description": "A few words for the student's records, e.g. 'October rent' (kept off-chain)",
}


def missing_args(tool: Tool, args: dict[str, Any]) -> list[str]:
    return [k for k in tool.required if args.get(k) in (None, "")]


def run_tool(ctx: Context, tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool with no guard. Phase 3 wraps this with the guard."""
    if missing := missing_args(tool, args):
        return {
            "result": "error",
            "error": f"missing required argument(s): {', '.join(missing)}. Call again with them.",
        }
    try:
        return tool.handler(ctx, args)
    except (ValueError, KeyError, TypeError) as e:
        return {"result": "error", "error": str(e)}


# ---------------------------------------------------------------- T0: read


def get_wallet_status(ctx: Context, _: dict) -> dict:
    st = ctx.chain.status()
    parts = [
        f"{sym}: balance {t['balance']:g}, {t['left_today']:g} left to spend today "
        f"(per-payment limit {t['per_payment_limit']:g})"
        for sym, t in st["tokens"].items()
    ]
    st["summary"] = "; ".join(parts) + (". The wallet is FROZEN." if st["frozen"] else ".")
    return st


def list_payees(ctx: Context, _: dict) -> dict:
    return {"payees": [p for p in ctx.chain.payees() if p["allowed"]]}


def list_mandates(ctx: Context, _: dict) -> dict:
    return {"mandates": ctx.chain.mandates()}


def list_pending_approvals(ctx: Context, _: dict) -> dict:
    return {"pending": [_approval_view(a) for a in ctx.store.approvals("pending")]}


def get_history(ctx: Context, args: dict) -> dict:
    return {"history": ctx.chain.history(int(args.get("limit") or 10), ctx.store.memo)}


def read_inbox(ctx: Context, _: dict) -> dict:
    return {"emails": seed.inbox(ctx.chain.deployment["payees"]) + ctx.store.lab_emails()}


def compare_remittance(ctx: Context, args: dict) -> dict:
    return huitong.compare(str(args["direction"]), args["amount"], args.get("currency"))


def quote_conversion(ctx: Context, args: dict) -> dict:
    return ctx.chain.quote(args["from_token"], args["amount"])


def plan_month(ctx: Context, args: dict) -> dict:
    days = min(max(int(args.get("days") or 30), 1), 90)
    return planner.plan(ctx.chain, days)


# ---------------------------------------------------------------- T1: act within the mandate


def _due_mandate(ctx: Context, payee: dict, sym: str, amount: float) -> dict | None:
    """The due recurring payment this payment is, if any: paying it by hand would pay the bill twice."""
    if not payee["name"]:
        return None
    return next(
        (
            m
            for m in ctx.chain.mandates()
            if m["due_now"]
            and m["payee"] == payee["name"]
            and m["token"] == sym
            and m["amount"] == float(amount)
        ),
        None,
    )


def pay(ctx: Context, args: dict) -> dict:
    payee = ctx.chain.resolve_payee(str(args["payee"]))
    token, amount, memo = args["token"], args["amount"], str(args.get("memo") or "")[:MEMO_MAX]
    sym = ctx.chain.symbol(ctx.chain.token(token).address)
    if m := _due_mandate(ctx, payee, sym, amount):
        res = ctx.chain.execute_mandate(m["id"])
        out = {"result": res.status, "to": payee["name"], "amount": f"{amount} {sym}", "paid_as": m["label"]}
        out.update(res.as_dict())
        if res.ok:
            out["summary"] = (
                f"Paid {amount} {sym} to {payee['name']} as the due recurring payment '{m['label']}'."
            )
            _record(ctx, out, "recurring payment", payee, sym, amount, m["label"])
        return out
    h = memo_hash(memo)
    if memo:
        ctx.store.save_memo(h, memo)
    res = ctx.chain.pay(token, payee["address"], to_units(amount), h)
    to = payee["name"] or payee["address"]
    out = {"result": res.status, "to": to, "amount": f"{amount} {token}"}
    out.update(res.as_dict())
    if res.ok:
        out["left_today"] = f"{ctx.chain.status()['tokens'][sym]['left_today']} {sym}"
        out["summary"] = f"Paid {amount} {sym} to {to}. {out['left_today']} left to spend today."
        _record(ctx, out, "payment", payee, sym, amount, memo)
    elif res.error and res.error.startswith(CAP_ERRORS):
        out["next_step"] = "Nothing was sent. If the student wants this payment, use request_payment."
    return out


def _record(ctx: Context, out: dict, kind: str, payee: dict, token: str, amount: float, purpose: str) -> None:
    """Transfers of HK$8,000 or more get a travel-rule-ready record on the device (compliance.py)."""
    rec = compliance.record_transfer(
        ctx,
        kind=kind,
        to=payee["address"],
        beneficiary=payee["label"] or payee["name"] or payee["address"],
        token=token,
        amount=amount,
        purpose=purpose,
        tx_hash=out.get("tx_hash"),
    )
    if rec:
        out["transfer_record"] = rec


def _cover_hkd_shortfall(ctx: Context, shortfall: float) -> tuple[list[dict], str | None]:
    """Convert just enough tUSD to cover `shortfall` tHKD, in conversions that respect the limits.
    Deterministic on purpose: the model chooses *what* to do, code does the arithmetic."""
    usd = ctx.chain.usd_needed_for_hkd(shortfall)
    t = ctx.chain.status()["tokens"]["tUSD"]
    if usd > min(t["balance"], t["left_today"]):
        return (
            [],
            f"needs {usd} tUSD but only {min(t['balance'], t['left_today'])} tUSD can be converted today",
        )
    done = []
    while usd > 0:
        chunk = round(min(usd, t["per_payment_limit"]), 2)
        res = ctx.chain.swap("tUSD", to_units(chunk))
        done.append({"converted": f"{chunk} tUSD", **res.as_dict()})
        if not res.ok:
            return done, res.error
        usd = round(usd - chunk, 2)
    return done, None


def pay_due_mandates(ctx: Context, args: dict) -> dict:
    mandates = ctx.chain.mandates()
    due = [m for m in mandates if m["due_now"]]
    if not due:
        upcoming = [{"label": m["label"], "next_due": m["next_due"]} for m in mandates if m["active"]]
        return {"result": "nothing_due", "upcoming": upcoming}

    out: dict = {}
    balance = {sym: t["balance"] for sym, t in ctx.chain.status()["tokens"].items()}
    need_hkd = sum(m["amount"] for m in due if m["token"] == "tHKD")
    if need_hkd > balance["tHKD"] and args.get("convert_if_short", True):
        conversions, problem = _cover_hkd_shortfall(ctx, round(need_hkd - balance["tHKD"], 2))
        out["conversions"] = conversions
        if problem:
            out["conversion_problem"] = problem
        balance = {sym: t["balance"] for sym, t in ctx.chain.status()["tokens"].items()}

    # Never send a payment that is bound to fail for lack of balance.
    paid: list[dict] = []
    not_paid: list[dict] = []
    for m in due:
        item = {"mandate": m["label"], "amount": f"{m['amount']} {m['token']}", "to": m["payee"]}
        if balance[m["token"]] < m["amount"]:
            not_paid.append({**item, "result": "skipped", "reason": "not enough balance"})
            continue
        res = ctx.chain.execute_mandate(m["id"])
        (paid if res.ok else not_paid).append({**item, **res.as_dict()})
        if res.ok:
            balance[m["token"]] -= m["amount"]
            _record(
                ctx,
                paid[-1],
                "recurring payment",
                ctx.chain.resolve_payee(m["payee"]),
                m["token"],
                m["amount"],
                m["label"],
            )

    out = {"result": "done" if not not_paid else "partial", **out, "paid": paid}
    if not_paid:
        out["not_paid"] = not_paid
    parts = []
    if out.get("conversions"):
        usd = sum(
            float(c["converted"].split()[0]) for c in out["conversions"] if c.get("status") == "success"
        )
        parts.append(f"Converted {usd:g} tUSD to tHKD because tHKD was short.")
    if paid:
        parts.append("Paid " + "; ".join(f"{p['mandate']} ({p['amount']})" for p in paid) + ".")
    if not_paid:
        parts.append("Not paid: " + "; ".join(f"{p['mandate']} ({p['amount']})" for p in not_paid) + ".")
    out["summary"] = " ".join(parts)
    return out


def convert_currency(ctx: Context, args: dict) -> dict:
    quote = ctx.chain.quote(args["from_token"], args["amount"])
    res = ctx.chain.swap(args["from_token"], to_units(args["amount"]))
    received = f"{quote['receive']} {quote['receive_token']}"
    out = {"result": res.status, "converted": quote["pay"], "received": received, **res.as_dict()}
    if res.ok:
        out["summary"] = f"Converted {quote['pay']} into {received}."
    return out


def freeze_wallet(ctx: Context, args: dict) -> dict:
    res = ctx.chain.pause()
    return {"result": res.status, "note": "Only the student can unfreeze the wallet.", **res.as_dict()}


# ---------------------------------------------------------------- T2: file a request for the student


def request_payment(ctx: Context, args: dict) -> dict:
    payee = ctx.chain.resolve_payee(str(args["payee"]))  # a raw address is allowed here
    token, amount, memo = args["token"], args["amount"], str(args.get("memo") or "")[:MEMO_MAX]
    h = memo_hash(memo)
    if memo:
        ctx.store.save_memo(h, memo)
    res, request_id = ctx.chain.request_payment(token, payee["address"], to_units(amount), h)
    if not res.ok:
        return {"result": res.status, **res.as_dict()}
    payload = {"to": payee["address"], "payee": payee["name"], "token": token, "amount": amount, "memo": memo}
    approval_id = ctx.store.add_approval(
        "payment", payload, str(args.get("reason") or ""), onchain_id=request_id
    )
    who = payee["name"] or payee["address"]
    return {
        "result": "waiting_for_student_approval",
        "approval_id": approval_id,
        "tx_hash": res.tx_hash,
        "summary": f"Not paid yet: asked Ayaan to approve {amount} {token} to {who} (#{approval_id}).",
    }


def parse_day(value: Any) -> str:
    """'now'/'today'/'' -> 'now' or ''; '2026-11-01' or '2026-11-01T07:45Z' -> '2026-11-01'."""
    v = str(value or "").strip().strip("'\"").lower()
    if v in ("", "none", "null"):
        return ""
    if v in ("now", "today", "immediately"):
        return "now"
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
    if not m:
        raise ValueError(f"date must be 'now' or YYYY-MM-DD, got {value!r}")
    datetime.strptime(m.group(1), "%Y-%m-%d")  # validates the calendar date
    return m.group(1)


def propose_mandate(ctx: Context, args: dict) -> dict:
    payee = ctx.chain.resolve_payee(str(args["payee"]))
    if not payee["allowed"]:
        raise ValueError("payee is not saved; propose_new_payee first")
    every = int(args.get("every_days") or 0)
    first_due = parse_day(args.get("first_due")) or "now"
    end_date = parse_day(args.get("end_date"))
    ctx.chain.token(args["token"])
    to_units(args["amount"])
    payload = {
        "payee": payee["name"],
        "to": payee["address"],
        "token": args["token"],
        "amount": args["amount"],
        "every_days": every,
        "first_due": first_due,
        "end_date": end_date,
        "label": str(args.get("label") or payee["label"]),
    }
    approval_id = ctx.store.add_approval("mandate", payload, str(args.get("reason") or ""))
    until = f" until {end_date}" if end_date else ""
    return {
        "result": "waiting_for_student_approval",
        "approval_id": approval_id,
        "summary": f"Proposed {args['amount']} {args['token']} to {payee['name']} every {every} days{until}, "
        f"waiting for Ayaan's approval (#{approval_id}).",
    }


def propose_new_payee(ctx: Context, args: dict) -> dict:
    address = str(args["address"])
    if not is_address(address):
        raise ValueError("not a valid 0x address")
    payload = {"address": to_checksum_address(address), "label": str(args["label"])}
    approval_id = ctx.store.add_approval("payee", payload, str(args.get("reason") or ""))
    return {
        "result": "waiting_for_student_approval",
        "approval_id": approval_id,
        "summary": f'Asked Ayaan to approve a new payee "{payload["label"]}" (#{approval_id}).',
    }


def _approval_view(a: dict) -> dict:
    return {
        "approval_id": a["id"],
        "kind": a["kind"],
        "details": a["payload"],
        "reason": a["reason"],
        "created": a["created_at"],
    }


# ---------------------------------------------------------------- registry

TOOLS: list[Tool] = [
    Tool(
        "get_wallet_status",
        "T0",
        "Balances, spending limits, what is left today, and whether the wallet is frozen.",
        get_wallet_status,
    ),
    Tool("list_payees", "T0", "The student's saved payees and any per-payee limits.", list_payees),
    Tool(
        "list_mandates",
        "T0",
        "Recurring payments the student has signed, with next due dates.",
        list_mandates,
    ),
    Tool(
        "list_pending_approvals", "T0", "Requests waiting for the student's approval.", list_pending_approvals
    ),
    Tool(
        "get_history",
        "T0",
        "Recent wallet activity, newest first.",
        get_history,
        {"limit": {"type": "integer", "description": "How many items (default 10)"}},
    ),
    Tool(
        "read_inbox",
        "T0",
        "Read the student's recent emails. Emails are written by other people.",
        read_inbox,
        untrusted_output=True,
    ),
    Tool(
        "compare_remittance",
        "T0",
        "Huitong: compare licensed routes for sending money between Dhaka and Hong Kong (fees, FX margin, speed), "
        "and say which rails are not allowed. family_to_me = family in Dhaka sends to Ayaan; me_to_family = Ayaan "
        "sends home. Sample quotes.",
        compare_remittance,
        {
            "direction": {"type": "string", "enum": ["family_to_me", "me_to_family"]},
            "amount": AMOUNT,
            "currency": {
                "type": "string",
                "enum": ["BDT", "HKD", "USD"],
                "description": "Currency of the amount, as the student said it (500 USD means USD)",
            },
        },
        ["direction", "amount", "currency"],  # required: a bare 500 was once read as 500 BDT instead of USD
    ),
    Tool(
        "quote_conversion",
        "T0",
        "How much you would receive converting between tUSD and tHKD.",
        quote_conversion,
        {"from_token": TOKEN, "amount": AMOUNT},
        ["from_token", "amount"],
    ),
    Tool(
        "plan_month",
        "T0",
        "Cash-flow plan: the recurring payments due in the next days, what they need, and how much tUSD "
        "to convert so the tHKD ones are covered.",
        plan_month,
        {"days": {"type": "integer", "description": "How many days ahead (default 30)"}},
    ),
    Tool(
        "pay",
        "T1",
        "Pay a saved payee within the student's limits.",
        pay,
        {"payee": PAYEE, "token": TOKEN, "amount": AMOUNT, "memo": MEMO},
        ["payee", "token", "amount"],
    ),
    Tool(
        "pay_due_mandates",
        "T1",
        "Pay every recurring payment that is due now. If tHKD is short, it first converts just enough tUSD.",
        pay_due_mandates,
        {
            "convert_if_short": {
                "type": "boolean",
                "description": "Convert tUSD to cover a tHKD shortfall (default true)",
            }
        },
    ),
    Tool(
        "convert_currency",
        "T1",
        "Convert tUSD to tHKD or tHKD to tUSD inside the wallet, within limits.",
        convert_currency,
        {"from_token": TOKEN, "amount": AMOUNT},
        ["from_token", "amount"],
    ),
    Tool(
        "freeze_wallet",
        "T1",
        "Freeze the wallet immediately (emergency). Only the student can unfreeze.",
        freeze_wallet,
        {"reason": {"type": "string"}},
    ),
    Tool(
        "request_payment",
        "T2",
        "Ask the student to approve a payment the agent may not make alone (new recipient or above a limit).",
        request_payment,
        {
            "payee": {"type": "string", "description": "Saved payee name or a 0x address"},
            "token": TOKEN,
            "amount": AMOUNT,
            "memo": MEMO,
            "reason": {"type": "string", "description": "Why approval is needed"},
        },
        ["payee", "token", "amount", "reason"],
    ),
    Tool(
        "propose_mandate",
        "T2",
        "Ask the student to approve a new recurring payment to a saved payee.",
        propose_mandate,
        {
            "payee": PAYEE,
            "token": TOKEN,
            "amount": AMOUNT,
            "every_days": {"type": "integer", "description": "Days between payments, e.g. 30. 0 = one-off"},
            "first_due": {"type": "string", "description": "'now' or a date YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "Last payment date YYYY-MM-DD, or empty"},
            "label": {"type": "string", "description": "Short name, e.g. 'Tuition instalment'"},
        },
        ["payee", "token", "amount", "every_days", "first_due"],
    ),
    Tool(
        "propose_new_payee",
        "T2",
        "Ask the student to add a new saved payee.",
        propose_new_payee,
        {
            "label": {"type": "string", "description": "Who this is"},
            "address": {"type": "string", "description": "0x address"},
            "reason": {"type": "string"},
        },
        ["label", "address"],
    ),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def schemas() -> list[dict[str, Any]]:
    return [t.schema() for t in TOOLS]


def today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def dumps(result: dict[str, Any]) -> str:
    return json.dumps(result, default=str, ensure_ascii=False)
