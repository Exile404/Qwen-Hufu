"""The autopilot and the cash-flow planner on the test chain (no model)."""

import pytest

from hufu import planner
from hufu.autopilot import Autopilot
from hufu.chain import to_units
from hufu.guard import Guard
from hufu.tools import TOOLS_BY_NAME

pytestmark = pytest.mark.integration


@pytest.fixture
def pilot(ctx, monkeypatch):
    monkeypatch.setattr("hufu.autopilot.GRACE_S", 0)
    p = Autopilot(ctx, Guard(ctx, anchor=False))
    p.set_enabled(True)
    return p


def kinds(run):
    return [e["kind"] for e in run["events"]]


def test_pays_what_is_due_and_flags_scam_emails_once(pilot, ctx):
    run = pilot.tick()
    assert "paid_due" in kinds(run)  # the phone plan is due at deployment
    assert not any(m["due_now"] for m in ctx.chain.mandates())
    scams = {e["detail"]["email_id"] for e in run["events"] if e["kind"] == "scam_email"}
    assert 4 in scams and 1 not in scams  # the fake fees office, not the real rent reminder
    assert pilot.tick()["events"] == []  # nothing is paid or raised twice


def test_tripwire_freezes_when_the_agent_key_is_used_behind_hufus_back(pilot, ctx):
    pilot.tick()
    landlord = ctx.chain.deployment["payees"]["landlord"]
    rogue = ctx.chain.pay("tHKD", landlord, to_units(500), "0x" + "00" * 32)  # stolen key, no guard
    assert rogue.ok
    run = pilot.tick()
    trip = next(e for e in run["events"] if e["kind"] == "tripwire")
    assert trip["level"] == "critical" and rogue.tx_hash in str(trip["detail"])
    assert ctx.chain.status()["frozen"]
    assert any(a["kind"] == "tripwire" and a["level"] == "critical" for a in ctx.store.alerts())


def test_payments_hufu_made_do_not_trip_the_wire(pilot, ctx):
    pilot.tick()
    pilot.guard.begin_turn("pay my landlord 4500 HKD for rent")
    r = pilot.guard.execute(ctx, TOOLS_BY_NAME["pay"], {"payee": "landlord", "token": "tHKD", "amount": 4500})
    assert r["result"] == "success"
    assert "tripwire" not in kinds(pilot.tick())
    assert not ctx.chain.status()["frozen"]


def test_converts_ahead_so_a_big_bill_is_covered_on_its_due_date(pilot, ctx):
    chain = ctx.chain
    university = chain.deployment["payees"]["university"]
    in_3_days = chain.now() + 3 * 86_400
    assert chain.create_mandate(university, "tHKD", to_units(9000), 30 * 86_400, in_3_days, 0, "Tuition").ok
    pilot.guard.begin_turn("pay my landlord 4500 HKD for rent")
    pilot.guard.execute(ctx, TOOLS_BY_NAME["pay"], {"payee": "landlord", "token": "tHKD", "amount": 4500})

    before = planner.plan(chain, 7)
    assert before["hkd_shortfall"] > 0 and before["covered"]
    run = pilot.tick()
    funded = next(e for e in run["events"] if e["kind"] == "funded")
    assert funded["detail"]["converted_usd"] == pytest.approx(before["convert_usd"], abs=0.01)
    assert planner.plan(chain, 7)["hkd_shortfall"] == 0
    assert "funded" not in kinds(pilot.tick())  # already covered: no second conversion


def test_does_nothing_but_alert_while_frozen(pilot, ctx):
    ctx.chain.pause(ctx.chain.guardian)
    run = pilot.tick()
    assert "paid_due" not in kinds(run)
    assert any(m["due_now"] for m in ctx.chain.mandates())


def test_plan_lists_what_is_coming_and_what_to_convert(ctx):
    p = planner.plan(ctx.chain, 30)
    assert p["upcoming"][0]["label"] == "Phone & data plan"
    assert p["needed"]["tHKD"] >= 198 and p["hkd_shortfall"] == 0 and p["covered"]
    assert "one-off bills" in p["note"]
