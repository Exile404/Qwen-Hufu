"""Huitong (汇通): compare licensed remittance routes for a student's corridor.

Named after the Shanxi remittance houses' motto 汇通天下. Two rules shape every answer:
  1. A route is only offered if it is legal at *both* ends (jurisdiction rules in remittance.yaml).
  2. Cost = fixed fees + receiving fees + the FX margin hidden in the rate, all shown in the
     receiving currency against the mid-market rate, so routes compare like for like.
Quotes are sample data, labelled as such everywhere they appear.
"""

from functools import lru_cache
from typing import Any

import yaml

from hufu.seed import SEED_DIR


@lru_cache
def data() -> dict[str, Any]:
    return yaml.safe_load((SEED_DIR / "remittance.yaml").read_text())


def _rate(frm: str, to: str) -> float:
    mid = data()["mid_per_usd"]
    return mid[to] / mid[frm]


def compare(direction: str, amount: float, currency: str | None = None) -> dict[str, Any]:
    """`direction`: family_to_me (Dhaka -> Hong Kong) or me_to_family (Hong Kong -> Dhaka).
    `amount` is what the sender pays, in `currency` (defaults to the corridor's pay currency)."""
    d = data()
    if direction not in d["corridors"]:
        raise ValueError(f"direction must be one of {', '.join(d['corridors'])}")
    c = d["corridors"][direction]
    pay_ccy, recv_ccy = c["pay_currency"], c["receive_currency"]
    currency = (currency or pay_ccy).upper().removeprefix("T")
    if currency not in d["mid_per_usd"]:
        raise ValueError(f"currency must be one of {', '.join(d['mid_per_usd'])}")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("amount must be greater than zero")
    pay = amount * _rate(currency, pay_ccy)  # what the sender pays, in the corridor's pay currency
    mid_value = pay * _rate(pay_ccy, recv_ccy)  # what the recipient would get at mid-market, no fees

    ends = [d["jurisdictions"][c["from"]], d["jurisdictions"][c["to"]]]
    routes, excluded = [], []
    for r in c["routes"]:
        if r["rail"] == "stablecoin":
            blocked = [j for j in ends if j["stablecoin"] != "allowed"]
            if blocked:
                excluded.append({"route": r["name"], "reason": " ".join(j["note"] for j in blocked)})
                continue
        if not r["licensed"]:
            excluded.append({"route": r["name"], "reason": "not a licensed channel"})
            continue
        fee_in_pay = r["fixed_fee"]["amount"] * _rate(r["fixed_fee"]["currency"], pay_ccy)
        converted = max(pay - fee_in_pay, 0) * _rate(pay_ccy, recv_ccy) * (1 - r["fx_margin_pct"] / 100)
        received = max(
            converted - r["receiving_fee"]["amount"] * _rate(r["receiving_fee"]["currency"], recv_ccy), 0
        )
        cost = mid_value - received
        routes.append(
            {
                "route": r["name"],
                "recipient_gets": round(received, 2),
                "total_cost": round(cost, 2),
                "cost_percent": round(cost / mid_value * 100, 2) if mid_value else 0,
                "speed": r["speed"],
                "note": r.get("note", ""),
            }
        )
    routes.sort(key=lambda x: -x["recipient_gets"])
    best = routes[0] if routes else None
    frm, to = d["jurisdictions"][c["from"]]["name"], d["jurisdictions"][c["to"]]["name"]
    summary = (
        f'Sending {pay:,.0f} {pay_ccy} from {frm} to {to}: cheapest licensed route is "{best["route"]}", '
        f"recipient gets about {best['recipient_gets']:,.2f} {recv_ccy} (cost {best['cost_percent']}%)."
        if best
        else "No licensed route available."
    )
    if excluded:
        summary += " Not offered: " + "; ".join(f"{e['route']} ({e['reason']})" for e in excluded)
    return {
        "corridor": f"{frm} -> {to}",
        "sender_pays": f"{pay:,.2f} {pay_ccy}",
        "mid_market_value": f"{mid_value:,.2f} {recv_ccy}",
        "routes": routes,
        "not_offered": excluded,
        "summary": summary,
        "source": f"{d['disclaimer']} Rates as of {d['as_of']}.",
    }
