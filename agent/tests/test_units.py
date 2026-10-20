"""Pure unit tests: no chain, no model."""

import pytest
from eth_utils import is_checksum_address

from hufu.chain import _error_table, lookalike, memo_hash, to_units
from hufu.seed import ATTACKER, render
from hufu.tools import TOOLS, schemas


def test_to_units():
    assert to_units(4500) == 4_500_000_000
    assert to_units("0.000001") == 1
    assert to_units(12.5) == 12_500_000
    for bad in (0, -1, "0.0000001"):
        with pytest.raises(ValueError):
            to_units(bad)


def test_lookalike_keeps_prefix_and_suffix():
    real = "0xD5dBcc7fb50E1379A497baF85c7ad4DE83Cb7D53"
    fake = lookalike(real)
    assert fake.lower() != real.lower()
    assert fake.lower()[:8] == real.lower()[:8]
    assert fake.lower()[-6:] == real.lower()[-6:]
    assert is_checksum_address(fake)


def test_render_placeholders():
    payees = {"landlord": "0xD5dBcc7fb50E1379A497baF85c7ad4DE83Cb7D53"}
    out = render("pay {attacker} not {lookalike:landlord} but {landlord}", payees)
    assert ATTACKER in out and payees["landlord"] in out and "{" not in out


def test_memo_hash_is_bytes32_hex():
    h = memo_hash("October rent")
    assert h.startswith("0x") and len(h) == 66


def test_error_table_selector():
    abi = [
        {
            "type": "error",
            "name": "OverPerTxCap",
            "inputs": [{"type": "uint256", "name": "amount"}, {"type": "uint256", "name": "cap"}],
        }
    ]
    table = _error_table([abi])
    ((selector, (name, types)),) = table.items()
    assert name == "OverPerTxCap" and types == ["uint256", "uint256"] and len(selector) == 10


def test_tool_registry():
    assert {t.tier for t in TOOLS} == {"T0", "T1", "T2"}
    names = [s["function"]["name"] for s in schemas()]
    assert len(names) == len(set(names))
    for s in schemas():
        params = s["function"]["parameters"]
        assert set(params["required"]) <= set(params["properties"])
    # No tool can change policy: that path does not exist for the agent (T3).
    forbidden = ("set_", "limit", "unfreeze", "unpause", "withdraw", "owner")
    assert not [n for n in names if any(f in n for f in forbidden)]


def test_huitong_excludes_stablecoins_for_bangladesh_and_ranks_routes():
    from hufu.huitong import compare

    out = compare("family_to_me", 500, "USD")
    assert out["sender_pays"] == "61,000.00 BDT"
    gets = [r["recipient_gets"] for r in out["routes"]]
    assert gets == sorted(gets, reverse=True) and all(g < 3900 for g in gets)  # every route costs something
    assert [e["route"] for e in out["not_offered"]] == ["Stablecoin transfer"]
    assert "Bangladesh" in out["not_offered"][0]["reason"]
    assert "Sample" in out["source"]
    back = compare("me_to_family", 2000)
    assert back["routes"][0]["route"].startswith("Licensed money service operator")
    with pytest.raises(ValueError):
        compare("to_the_moon", 1)


def test_store_forgets_a_vaults_rows_when_its_deployment_changes(tmp_path):
    from hufu.store import Store

    s = Store(tmp_path / "t.db", "0xVAULT")
    assert s.bind_deployment("31337:5:0xaaa") is False  # first sight: nothing to forget
    s.add_approval("payee", {"address": "0x1"})
    s.add_alert("warning", "scam_email", "x", {}, "email:4")
    other = Store(tmp_path / "t.db", "0xOTHER")
    other.add_approval("payee", {"address": "0x2"})
    assert s.bind_deployment("31337:5:0xaaa") is False and len(s.approvals()) == 1  # same chain
    assert s.bind_deployment("31337:5:0xbbb") is True  # fresh chain, same vault address
    assert s.approvals() == [] and s.alerts() == [] and len(other.approvals()) == 1


def test_store_and_audit_are_safe_across_threads(tmp_path):
    """Dashboard polls racing the autopilot thread once failed with "bad parameter or other API misuse"."""
    import threading

    from hufu.guard.audit import AuditLog
    from hufu.store import Store

    store = Store(tmp_path / "hufu.db", "0x" + "11" * 20)
    audit = AuditLog(store.db, store.vault)
    errors: list[Exception] = []

    def work(i: int) -> None:
        try:
            for j in range(50):
                store.put(f"k{i}", j)
                assert store.get(f"k{i}") == j
                audit.append("tick", {"thread": i, "n": j})
                audit.recent(5)
                audit.mentions("0xabc")
        except Exception as e:  # noqa: BLE001 - collect any failure from any thread
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert audit.verify() == {"ok": True, "entries": 400, "head": audit.head()}
