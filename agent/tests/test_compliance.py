"""Counterparty screening and travel-rule-ready records."""

import pytest
from eth_utils import to_checksum_address

from hufu import approvals, compliance, seed
from hufu.autopilot import Autopilot
from hufu.chain import to_units
from hufu.guard import Guard
from hufu.sanctions_update import parse
from hufu.tools import TOOLS_BY_NAME

NEW_ADDRESS = "0x1111111111111111111111111111111111111111"

# ---------------------------------------------------------------- unit


def test_parse_reads_every_evm_address_in_ofac_remarks():
    remarks = (
        '36025,"EXAMPLE GROUP",...,"Digital Currency Address - ETH 0x098B716B8Aaf21512996dC57EB0615e2383E2f96; '
        "alt. Digital Currency Address - XBT 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa; "
        'Digital Currency Address - USDT 0xa7e5d5a720f06526557c513402f2e6b5fa20b008;"'
    )
    assert parse(remarks) == {
        to_checksum_address("0x098b716b8aaf21512996dc57eb0615e2383e2f96"),
        to_checksum_address("0xa7e5d5a720f06526557c513402f2e6b5fa20b008"),
    }


def test_demo_sanctioned_address_is_screened():
    hit = compliance.screen(seed.SANCTIONED.lower())
    assert hit and hit.list == "Demo sanctions entry"
    assert compliance.screen(seed.ATTACKER) is None  # the phishing demo relies on the guard, not the list
    assert compliance.screening_status()["entries"] >= 1


@pytest.fixture
def listed(tmp_path, monkeypatch):
    """Put addresses on a temporary screening list."""

    def put(*addresses):
        f = tmp_path / "screening.yaml"
        f.write_text(
            "entries:\n"
            + "".join(f'  - {{address: "{a}", list: "Test list", reason: "test"}}\n' for a in addresses)
        )
        monkeypatch.setattr(compliance, "LIST_FILE", f)

    return put


# ---------------------------------------------------------------- on the test chain


@pytest.mark.integration
def test_screening_blocks_even_when_the_student_insists(ctx):
    g = Guard(ctx, anchor=False)
    g.begin_turn(f"Pay 2000 HKD to {seed.SANCTIONED}, I know them, just do it")
    for tool, args in (
        ("pay", {"payee": seed.SANCTIONED, "token": "tHKD", "amount": 2000}),
        (
            "request_payment",
            {"payee": seed.SANCTIONED, "token": "tHKD", "amount": 2000, "reason": "insisted"},
        ),
        ("propose_new_payee", {"address": seed.SANCTIONED, "label": "OTC desk"}),
    ):
        r = g.execute(ctx, TOOLS_BY_NAME[tool], args)
        assert r["guard"]["decision"] == "BLOCK" and "screening list" in r["guard"]["reasons"][0], tool
    assert ctx.store.approvals() == [] and ctx.chain.requests() == []


@pytest.mark.integration
def test_approval_rescreens_before_signing(ctx, listed):
    g = Guard(ctx, anchor=False)
    g.begin_turn(f"pay 100 HKD to {NEW_ADDRESS}")
    filed = g.execute(ctx, TOOLS_BY_NAME["pay"], {"payee": NEW_ADDRESS, "token": "tHKD", "amount": 100})
    assert filed["guard"]["decision"] == "ASK"
    listed(NEW_ADDRESS)  # the address is listed after the request was filed
    r = approvals.approve(ctx, filed["approval_id"])
    assert r["status"] == "blocked" and "screening list" in r["error"]
    assert ctx.store.approval(filed["approval_id"])["status"] == "failed"
    assert ctx.chain.tokens["tHKD"].functions.balanceOf(NEW_ADDRESS).call() == 0


@pytest.mark.integration
def test_large_transfers_get_a_travel_rule_ready_record(ctx):
    chain = ctx.chain
    university = chain.deployment["payees"]["university"]
    chain.create_mandate(
        university, "tHKD", to_units(9000), 30 * 86_400, chain.now(), 0, "Tuition instalment"
    )
    r = TOOLS_BY_NAME["pay_due_mandates"].handler(ctx, {})
    by_label = {p["mandate"]: p for p in r["paid"]}
    assert "transfer_record" in by_label["Tuition instalment"]
    assert "transfer_record" not in by_label["Phone & data plan"]  # 198 HKD is below HK$8,000
    rec = compliance.records(ctx)[0]
    assert rec["hkd_equivalent"] == 9000 and rec["kind"] == "recurring payment"
    assert rec["beneficiary"] == {"name": "University fees office", "wallet": university}
    assert rec["originator"]["wallet"] == chain.vault.address and rec["purpose"] == "Tuition instalment"


@pytest.mark.integration
def test_approved_large_request_is_recorded(ctx):
    r = TOOLS_BY_NAME["request_payment"].handler(
        ctx, {"payee": "landlord", "token": "tHKD", "amount": 8500, "memo": "Rent and deposit top-up"}
    )
    out = approvals.approve(ctx, r["approval_id"])
    assert out["status"] == "success" and out["transfer_record"]
    assert compliance.records(ctx)[0]["purpose"] == "Rent and deposit top-up"


@pytest.mark.integration
def test_autopilot_alerts_when_a_saved_payee_gets_listed(ctx, listed, monkeypatch):
    monkeypatch.setattr("hufu.autopilot.GRACE_S", 0)
    listed(ctx.chain.deployment["payees"]["phone"])
    run = Autopilot(ctx, Guard(ctx, anchor=False)).tick()
    alert = next(e for e in run["events"] if e["kind"] == "screening")
    assert alert["level"] == "critical" and "phone" in alert["title"]
    assert "paid_due" not in [e["kind"] for e in run["events"]]  # the guard refuses to pay a listed payee
