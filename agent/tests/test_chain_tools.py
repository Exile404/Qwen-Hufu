"""Tools and approvals against a real (local) chain. No LLM involved."""

import pytest

from hufu import approvals, seed
from hufu.tools import TOOLS_BY_NAME, run_tool

pytestmark = pytest.mark.integration


def call(ctx, name, **args):
    return run_tool(ctx, TOOLS_BY_NAME[name], args)


def hkd_balance(ctx):
    return ctx.chain.status()["tokens"]["tHKD"]["balance"]


def test_status_reads_seeded_state(ctx):
    s = call(ctx, "get_wallet_status")
    assert s["tokens"]["tHKD"]["balance"] == 10_000
    assert s["tokens"]["tHKD"]["per_payment_limit"] == 5_000
    assert s["tokens"]["tUSD"]["balance"] == 800
    assert s["frozen"] is False


def test_pay_rent_within_limits(ctx):
    r = call(ctx, "pay", payee="landlord", token="tHKD", amount=4500, memo="October rent")
    assert r["result"] == "success", r
    assert r["tx_hash"].startswith("0x")
    assert hkd_balance(ctx) == 5_500
    latest = call(ctx, "get_history", limit=1)["history"][0]
    assert latest["kind"] == "payment" and latest["memo"] == "October rent"


def test_pay_unknown_address_is_refused_before_sending(ctx):
    r = call(ctx, "pay", payee=seed.ATTACKER, token="tHKD", amount=100)
    assert r["result"] == "rejected"
    assert r["error"].startswith("PayeeNotAllowed")
    assert "tx_hash" not in r
    assert "request_payment" in r["next_step"]
    assert hkd_balance(ctx) == 10_000


def test_pay_over_payee_cap_is_refused(ctx):
    r = call(ctx, "pay", payee="roommate", token="tHKD", amount=800)
    assert r["result"] == "rejected"
    assert r["error"] == "OverPayeeCap(800.0, 500.0)"


def test_paying_a_due_bill_by_hand_runs_its_recurring_payment(ctx):
    r = call(ctx, "pay", payee="phone", token="tHKD", amount=198)
    assert r["result"] == "success" and r["paid_as"] == "Phone & data plan"
    phone = next(m for m in ctx.chain.mandates() if m["payee"] == "phone")
    assert not phone["due_now"]  # so "pay everything due" will not pay it again
    assert call(ctx, "pay_due_mandates")["result"] == "nothing_due"


def test_split_payments_cannot_pass_the_payee_daily_cap(ctx):
    assert call(ctx, "pay", payee="roommate", token="tHKD", amount=300)["result"] == "success"
    r = call(ctx, "pay", payee="roommate", token="tHKD", amount=300)
    assert r["error"] == "OverPayeeCap(300.0, 200.0)"
    roommate = next(p for p in ctx.chain.payees() if p["name"] == "roommate")
    assert roommate["caps"] == {"tHKD": 500.0} and roommate["left_today"] == {"tHKD": 200.0}


def test_forced_attack_lands_as_reverted_transaction(ctx):
    res = ctx.chain.pay("tHKD", seed.ATTACKER, 5_000 * 10**6, "0x" + "00" * 32, force=True)
    assert res.status == "reverted"
    assert res.tx_hash and res.block
    assert res.error.startswith("PayeeNotAllowed")
    assert hkd_balance(ctx) == 10_000


def test_request_payment_then_student_approves(ctx):
    r = call(
        ctx,
        "request_payment",
        payee="university",
        token="tHKD",
        amount=9000,
        reason="over the per-payment limit",
    )
    assert r["result"] == "waiting_for_student_approval"
    assert hkd_balance(ctx) == 10_000  # nothing moves until the student signs
    pending = call(ctx, "list_pending_approvals")["pending"]
    assert pending[0]["approval_id"] == r["approval_id"]

    out = approvals.approve(ctx, r["approval_id"])
    assert out["status"] == "success"
    assert hkd_balance(ctx) == 1_000
    assert call(ctx, "list_pending_approvals")["pending"] == []


def test_request_payment_rejected_moves_nothing(ctx):
    r = call(ctx, "request_payment", payee=seed.ATTACKER, token="tHKD", amount=9000, reason="new account")
    approvals.reject(ctx, r["approval_id"])
    assert hkd_balance(ctx) == 10_000
    assert ctx.chain.requests()[0]["status"] == "rejected"
    with pytest.raises(ValueError):
        approvals.approve(ctx, r["approval_id"])


def test_tuition_mandate_flow(ctx):
    r = call(
        ctx,
        "propose_mandate",
        payee="university",
        token="tHKD",
        amount=9000,
        every_days=30,
        first_due="now",
        end_date="2027-06-01",
        label="Tuition instalment",
    )
    assert r["result"] == "waiting_for_student_approval"
    assert approvals.approve(ctx, r["approval_id"])["status"] == "success"

    tuition = next(m for m in ctx.chain.mandates() if m["label"] == "Tuition instalment")
    assert tuition["due_now"] and tuition["amount"] == 9000

    # 10,000 tHKD covers phone (198) + tuition (9,000)
    paid = call(ctx, "pay_due_mandates")
    assert paid["result"] == "done"
    assert {p["mandate"] for p in paid["paid"]} == {"Phone & data plan", "Tuition instalment"}
    assert all(p["status"] == "success" for p in paid["paid"])
    assert call(ctx, "pay_due_mandates")["result"] == "nothing_due"


def test_due_mandates_short_of_hkd(ctx):
    call(ctx, "pay", payee="landlord", token="tHKD", amount=4500)  # 5,500 tHKD left
    r = call(
        ctx,
        "propose_mandate",
        payee="university",
        token="tHKD",
        amount=9000,
        every_days=30,
        first_due="now",
        label="Tuition instalment",
    )
    approvals.approve(ctx, r["approval_id"])

    # Without conversion: phone is paid, tuition is skipped, and no doomed transaction is sent.
    out = call(ctx, "pay_due_mandates", convert_if_short=False)
    assert out["result"] == "partial"
    assert [p["mandate"] for p in out["paid"]] == ["Phone & data plan"]
    assert out["not_paid"][0]["reason"] == "not enough balance"

    # With conversion (the default): converts exactly enough tUSD, then pays tuition.
    out = call(ctx, "pay_due_mandates")
    assert out["result"] == "done", out
    assert out["conversions"] == [
        {"converted": "474.58 tUSD", **{k: out["conversions"][0][k] for k in ("status", "tx_hash", "block")}}
    ]
    assert out["paid"][0]["mandate"] == "Tuition instalment"
    s = ctx.chain.status()["tokens"]
    assert s["tUSD"]["balance"] == pytest.approx(800 - 474.58)
    assert 0 <= s["tHKD"]["balance"] < 1  # 5,302 + 3,698.x - 9,000


def test_convert_currency(ctx):
    q = call(ctx, "quote_conversion", from_token="tUSD", amount=500)
    assert q["receive"] == 3896.1
    r = call(ctx, "convert_currency", from_token="tUSD", amount=500)
    assert r["result"] == "success"
    assert hkd_balance(ctx) == pytest.approx(13_896.1)


def test_freeze_blocks_agent_until_student_unfreezes(ctx):
    assert call(ctx, "freeze_wallet", reason="lost phone")["result"] == "success"
    r = call(ctx, "pay", payee="landlord", token="tHKD", amount=10)
    assert r["result"] == "rejected" and r["error"] == "IsPaused()"
    assert ctx.chain.unpause().ok
    assert call(ctx, "pay", payee="landlord", token="tHKD", amount=10)["result"] == "success"


def test_inbox_renders_live_addresses(ctx):
    emails = call(ctx, "read_inbox")["emails"]
    bodies = " ".join(e["body"] for e in emails)
    assert seed.ATTACKER in bodies
    assert "{" not in bodies
