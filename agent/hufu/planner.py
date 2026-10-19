"""Cash-flow planning: what the signed recurring payments will need over the next days, and how much
tUSD has to be converted so the tHKD ones never fail on their due date.

Deterministic on purpose, like the rest of the money arithmetic: the model asks for a plan, code
computes it. Only recurring payments the student signed are counted; one-off bills such as rent are
paid when the student asks, so the plan says so instead of guessing them.
"""

import math
from datetime import UTC, datetime
from typing import Any

from hufu.chain import Chain

DAY = 86_400


def upcoming(chain: Chain, days: int) -> list[dict[str, Any]]:
    """Every occurrence of a signed recurring payment due within `days` (overdue ones included)."""
    until = chain.now() + days * DAY
    out = []
    for m in chain.mandate_schedule():
        due = m["next_due"]
        while due <= until and (m["end"] == 0 or due <= m["end"]):
            out.append(
                {
                    "label": m["label"],
                    "payee": m["payee"],
                    "token": m["token"],
                    "amount": m["amount"],
                    "due": datetime.fromtimestamp(due, UTC).strftime("%Y-%m-%d"),
                    "due_ts": due,
                }
            )
            if m["interval"] == 0:
                break
            due += m["interval"]
    return sorted(out, key=lambda x: x["due_ts"])


def plan(chain: Chain, days: int = 30) -> dict[str, Any]:
    tokens = chain.status()["tokens"]
    items = upcoming(chain, days)
    need = {sym: round(sum(i["amount"] for i in items if i["token"] == sym), 2) for sym in tokens}
    hkd_short = round(max(0.0, need["tHKD"] - tokens["tHKD"]["balance"]), 2)
    usd_spare = round(tokens["tUSD"]["balance"] - need["tUSD"], 2)
    convert = chain.usd_needed_for_hkd(hkd_short) if hkd_short else 0.0
    covered = convert <= usd_spare
    today = min(convert, tokens["tUSD"]["left_today"], max(usd_spare, 0.0))
    days_to_convert = math.ceil(convert / tokens["tUSD"]["daily_limit"]) if convert else 0

    if not items:
        summary = f"No recurring payments are due in the next {days} days."
    else:
        first = items[0]
        summary = (
            f"{len(items)} recurring payment(s) in the next {days} days: "
            + ", ".join(f"{i['token'][1:]} {i['amount']:,.0f} {i['label']} on {i['due']}" for i in items[:4])
            + ("…" if len(items) > 4 else "")
            + ". "
        )
        if not hkd_short:
            summary += "The tHKD balance already covers them."
        elif covered:
            summary += (
                f"tHKD is {hkd_short:,.2f} short: convert {convert:,.2f} tUSD"
                + (
                    f" over {days_to_convert} days (daily limit)"
                    if days_to_convert > 1
                    else f" before {first['due']}"
                )
                + "."
            )
        else:
            summary += (
                f"tHKD is {hkd_short:,.2f} short and there is not enough tUSD to cover it "
                f"({max(usd_spare, 0):,.2f} spare, {convert:,.2f} needed)."
            )
    return {
        "days": days,
        "upcoming": [{k: v for k, v in i.items() if k != "due_ts"} for i in items],
        "needed": need,
        "balances": {sym: t["balance"] for sym, t in tokens.items()},
        "hkd_shortfall": hkd_short,
        "convert_usd": convert,
        "convert_today_max": round(today, 2),
        "days_to_convert": days_to_convert,
        "covered": covered,
        "note": "Counts signed recurring payments only; one-off bills such as rent are not included.",
        "summary": summary,
    }
