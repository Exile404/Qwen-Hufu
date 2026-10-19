"""Autopilot: the agent acting on its own between conversations.

Each run, in order:
  1. Tripwire. Money that left the vault through the agent key but is not in Hufu's audit log means
     someone else holds the key. Freeze at once (the beacon-fire rule: stopping needs no approval)
     and alert the student, who alone can unfreeze and rotate the key.
  2. Pay the recurring payments that are due: amounts and payees the student signed on-chain.
  3. Keep the next FUND_DAYS of recurring payments funded: if tHKD will be short, convert just enough
     tUSD now, within today's limits, so a big bill never fails on its due date because the daily
     conversion limit was hit that morning.
  4. Read new emails with the guard's scanner and alert the student about high-risk ones.

No model is involved: the steps are code, and each action goes through the same guard as chat, lands
in the hash-chained audit log, and is anchored on-chain. The autopilot cannot pay anyone the student
did not sign up front: the guard has no payment request to match, so a plain `pay` is not available.
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from hufu import planner, seed
from hufu.guard import Guard
from hufu.guard.scanner import scan
from hufu.tools import TOOLS_BY_NAME, Context

FUND_DAYS = 7
GRACE_S = 2.0  # a payment made by another Hufu process may be mined a moment before it is logged


@dataclass
class Event:
    level: str  # info | action | warning | critical
    kind: str
    title: str
    detail: dict[str, Any] = field(default_factory=dict)
    dedupe: str | None = None


class Autopilot:
    def __init__(self, ctx: Context, guard: Guard, enabled: bool = False, interval_s: int = 20):
        self.ctx, self.guard, self.interval_s = ctx, guard, interval_s
        self.enabled = bool(ctx.store.get("autopilot_enabled", enabled))

    @property
    def chain(self):
        return self.ctx.chain

    def set_enabled(self, on: bool) -> None:
        if on and self.ctx.store.get("tripwire_from_block") is None:
            self.ctx.store.put("tripwire_from_block", self.chain.block_number() + 1)
        self.enabled = on
        self.ctx.store.put("autopilot_enabled", on)
        self.guard.audit.append("autopilot_switched", {"enabled": on})

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "last_run": self.ctx.store.get("autopilot_last_run"),
            "alerts": self.ctx.store.alerts(20),
        }

    # ------------------------------------------------------------------ one run

    def tick(self) -> dict[str, Any]:
        started = datetime.now(UTC).isoformat(timespec="seconds")
        self.guard.begin_system_turn("autopilot")
        events: list[Event] = []
        try:
            events += self._tripwire()
            if not self.chain.status()["frozen"]:
                events += self._pay_due()
                events += self._fund_upcoming()
            events += self._scan_inbox()
        except Exception as e:  # noqa: BLE001 - a failed run is reported, never fatal
            events.append(Event("warning", "error", f"Autopilot run failed: {type(e).__name__}: {e}"))
        anchor = self.guard.end_system_turn()

        for ev in events:
            if ev.level != "info":
                self.ctx.store.add_alert(ev.level, ev.kind, ev.title, ev.detail, ev.dedupe)
        run = {
            "at": started,
            "events": [{k: v for k, v in asdict(e).items() if k != "dedupe"} for e in events],
            "anchor": anchor,
        }
        self.guard.audit.append("autopilot_result", run)
        self.ctx.store.put("autopilot_last_run", run)
        return run

    # ------------------------------------------------------------------ steps

    def _tripwire(self) -> list[Event]:
        start = self.ctx.store.get("tripwire_from_block")
        latest = self.chain.block_number()
        if start is None or start > latest + 1:  # first run, or the local chain was reset
            self.ctx.store.put("tripwire_from_block", latest + 1)
            return []
        if start > latest:  # no new blocks since the last run
            return []
        outflows = self.chain.outflows(start, latest)
        unknown = [o for o in outflows if not self.guard.audit.mentions(o["tx_hash"])]
        if unknown:
            time.sleep(GRACE_S)
            unknown = [o for o in unknown if not self.guard.audit.mentions(o["tx_hash"])]
        self.ctx.store.put("tripwire_from_block", latest + 1)
        if not unknown:
            return []

        already = self.chain.status()["frozen"]
        frozen = {"result": "already frozen"}
        if not already:
            frozen = self.guard.execute(self.ctx, TOOLS_BY_NAME["freeze_wallet"], {})
        what = "; ".join(o["what"] for o in unknown)
        return [
            Event(
                "critical",
                "tripwire",
                f"Wallet frozen: the agent key moved money Hufu did not send ({what}). "
                "Someone else may have the key. Unfreeze only after rotating it.",
                {"unknown_transactions": unknown, "freeze": frozen},
                dedupe="tripwire:" + unknown[0]["tx_hash"],
            )
        ]

    def _pay_due(self) -> list[Event]:
        if not any(m["due_now"] for m in self.chain.mandates()):
            return []
        r = self.guard.execute(self.ctx, TOOLS_BY_NAME["pay_due_mandates"], {})
        txs = [p.get("tx_hash") for p in r.get("paid", [])] + [
            c.get("tx_hash") for c in r.get("conversions", [])
        ]
        events = []
        if r.get("paid"):
            events.append(
                Event(
                    "action",
                    "paid_due",
                    r.get("summary") or "Paid the recurring payments that were due.",
                    {"paid": r.get("paid"), "conversions": r.get("conversions", [])},
                    dedupe="tx:" + str(txs[0]),
                )
            )
        if r.get("not_paid") or r.get("result") == "blocked_by_guard":
            reasons = r.get("not_paid") or r.get("reasons")
            events.append(
                Event(
                    "warning",
                    "not_paid",
                    "A recurring payment is due but could not be paid: "
                    + "; ".join(
                        f"{p.get('mandate')} ({p.get('reason') or p.get('error')})"
                        for p in r.get("not_paid", [])
                    )
                    if r.get("not_paid")
                    else f"Recurring payments are due but the guard stopped them: {reasons}",
                    {"result": r},
                    dedupe="not_paid:" + datetime.now(UTC).strftime("%Y-%m-%d"),
                )
            )
        return events

    def _fund_upcoming(self) -> list[Event]:
        p = planner.plan(self.chain, FUND_DAYS)
        if p["convert_usd"] <= 0:
            return []
        usd = self.chain.status()["tokens"]["tUSD"]
        can = round(min(p["convert_usd"], p["convert_today_max"]), 2)
        bills = ", ".join(
            f"{i['label']} {i['amount']:,.0f} {i['token']} on {i['due']}" for i in p["upcoming"]
        )
        if can <= 0:
            return [
                Event(
                    "warning",
                    "funding_short",
                    f"Upcoming bills ({bills}) need {p['convert_usd']:,.2f} tUSD converted, "
                    "but nothing more can be converted today.",
                    {"plan": p},
                    dedupe="funding_short:" + datetime.now(UTC).strftime("%Y-%m-%d"),
                )
            ]
        done = 0.0
        txs: list[str | None] = []
        while round(can - done, 2) > 0:
            chunk = round(min(can - done, usd["per_payment_limit"]), 2)
            r = self.guard.execute(
                self.ctx, TOOLS_BY_NAME["convert_currency"], {"from_token": "tUSD", "amount": chunk}
            )
            if r.get("result") != "success":
                break
            done, txs = round(done + chunk, 2), [*txs, r.get("tx_hash")]
        if not done:
            return [
                Event("warning", "funding_failed", "Could not convert tUSD for upcoming bills.", {"plan": p})
            ]
        rest = round(p["convert_usd"] - done, 2)
        title = f"Converted {done:,.2f} tUSD so upcoming bills are covered ({bills})."
        if rest > 0:
            title += f" {rest:,.2f} tUSD more tomorrow (daily limit)."
        return [
            Event(
                "action",
                "funded",
                title,
                {"converted_usd": done, "tx_hashes": txs, "plan": p},
                "tx:" + str(txs[0]),
            )
        ]

    def _scan_inbox(self) -> list[Event]:
        seen = set(self.ctx.store.get("autopilot_seen_emails", []))
        emails = seed.inbox(self.chain.deployment["payees"]) + self.ctx.store.lab_emails()
        new = [e for e in emails if e["id"] not in seen]
        if not new:
            return []
        saved = {p["name"]: p["address"] for p in self.chain.payees() if p["allowed"]}
        events = []
        for e in new:
            rating = scan(f"{e['from']}\n{e['subject']}\n{e['body']}", saved).summary()
            if rating["risk"] == "high":
                events.append(
                    Event(
                        "warning",
                        "scam_email",
                        f"Likely scam email from {e['from']}: “{e['subject']}”. Don't pay anything it asks for.",
                        {"email_id": e["id"], "findings": rating["findings"]},
                        dedupe=f"email:{e['id']}",
                    )
                )
        self.ctx.store.put("autopilot_seen_emails", sorted(seen | {e["id"] for e in new}))
        return events
