"""The student's half as a passkey: WebAuthn parsing (offline) and the full flow through the API and the
vault on a private chain, signed by a software passkey that produces the same format as a phone."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hufu import api, passkey
from hufu.agent import Agent
from hufu.autopilot import Autopilot
from hufu.guard import Guard
from hufu.passkey import P256_N, Assertion, SoftwarePasskey, b64url
from hufu.tools import TOOLS_BY_NAME

# ---------------------------------------------------------------- offline: reading WebAuthn responses


def test_software_passkey_signature_verifies():
    pk = SoftwarePasskey()
    a = pk.sign(b"\x01" * 32)
    assert passkey.verify(a, *pk.public_key)
    assert not passkey.verify(a, *SoftwarePasskey().public_key)
    assert passkey.public_key_from_spki(pk.spki()) == pk.public_key


def test_vault_auth_finds_fields_and_uses_low_s():
    pk, challenge = SoftwarePasskey(), bytes(range(32))
    for _ in range(8):  # about half of raw ECDSA signatures are high-s
        a = pk.sign(challenge)
        r, s, challenge_index, type_index, auth_data, client_json = passkey.to_vault_auth(a, challenge)
        assert int.from_bytes(s, "big") <= P256_N // 2
        assert client_json[type_index:].startswith('"type":"webauthn.get"')
        assert client_json[challenge_index:].startswith(f'"challenge":"{b64url(challenge)}"')
        assert auth_data == a.authenticator_data and len(r) == 32


def test_vault_auth_uses_byte_offsets_with_extra_fields():
    """Browsers may add fields; the vault needs byte offsets into the exact JSON that was signed."""
    challenge = b"\x07" * 32
    raw = (
        '{"type":"webauthn.get","challenge":"' + b64url(challenge) + '","origin":"http://localhost:3000",'
        '"other_keys_can_be_added_here":"do not compare clientDataJSON against a template"}'
    ).encode()
    a = Assertion(b"\x00" * 32 + b"\x05" + b"\x00" * 4, raw, SoftwarePasskey().sign(challenge).signature)
    _, _, challenge_index, type_index, _, _ = passkey.to_vault_auth(a, challenge)
    assert (type_index, challenge_index) == (1, 23)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda a, c: (
                a.__setattr__("client_data_json", a.client_data_json.replace(b"get", b"create")),
                c,
            ),
            "sign-in",
        ),
        (lambda a, c: (a, b"\x09" * 32), "different challenge"),
        (lambda a, c: (a.__setattr__("authenticator_data", a.authenticator_data[:33]), c), "too short"),
        (lambda a, c: (a.__setattr__("signature", b"\x30\x02\x01\x01"), c), "DER"),
        (lambda a, c: (a.__setattr__("client_data_json", b"\xff\xfe"), c), "client data"),
    ],
)
def test_vault_auth_refuses_bad_responses(mutate, message):
    challenge = b"\x08" * 32
    a = SoftwarePasskey().sign(challenge)
    _, expected = mutate(a, challenge)
    with pytest.raises(ValueError, match=message):
        passkey.to_vault_auth(a, expected)


def test_vault_auth_requires_user_verification():
    challenge = b"\x0a" * 32
    with pytest.raises(ValueError, match="fingerprint, face or PIN"):
        passkey.to_vault_auth(SoftwarePasskey().sign(challenge, user_verified=False), challenge)


def test_browser_round_trip_and_bad_inputs():
    a = SoftwarePasskey().sign(b"\x0b" * 32)
    assert Assertion.from_browser(a.to_browser()) == a
    with pytest.raises(ValueError):
        Assertion.from_browser({"signature": "AA"})
    with pytest.raises(ValueError):
        passkey.public_key_from_spki(b"not a key")
    with pytest.raises(ValueError):
        passkey.der_to_rs(b"\x30\x06\x02\x01\x00\x02\x01\x01")  # r = 0


# ---------------------------------------------------------------- on-chain, through the API


@pytest.fixture
def client(ctx, monkeypatch):
    guard = Guard(ctx, anchor=False)
    runtime = SimpleNamespace(
        chain=ctx.chain, ctx=ctx, guard=guard, agent=Agent(ctx, guard=guard), autopilot=Autopilot(ctx, guard)
    )
    monkeypatch.setattr(api, "rt", lambda: runtime)
    c = TestClient(api.app)
    c.runtime = runtime
    return c


def _signed(pk: SoftwarePasskey, challenge: dict) -> dict:
    a = pk.sign(passkey.b64url_decode(challenge["challenge"]))
    return {"challenge_id": challenge["challenge_id"], "assertion": a.to_browser()}


def _as_student(client, pk: SoftwarePasskey, path: str, body: dict | None = None) -> dict:
    """What the dashboard does: try, get the 428 challenge, sign it with the passkey, repeat."""
    first = client.post(path, json=body or {})
    assert first.status_code == 428, first.text
    challenge = first.json()["passkey"]
    second = client.post(path, json=(body or {}) | {"signed": _signed(pk, challenge)})
    assert second.status_code == 200, second.text
    return second.json()


def _register(client, pk: SoftwarePasskey) -> dict:
    body = {"credential_id": b64url(pk.credential_id), "public_key": b64url(pk.spki())}
    return client.post("/passkey", json=body).json()


def _escalated_payment(client, amount: int = 6_000) -> int:
    g = client.runtime.guard
    g.begin_turn(f"pay the landlord {amount} HKD for the deposit")
    r = g.execute(
        client.runtime.ctx,
        TOOLS_BY_NAME["pay"],
        {"payee": "landlord", "token": "tHKD", "amount": amount, "memo": "deposit"},
    )
    return r["approval_id"]


pytestmark_chain = pytest.mark.integration


@pytestmark_chain
def test_register_then_approvals_need_the_passkey(client):
    pk = SoftwarePasskey()
    assert client.get("/state").json()["passkey"]["registered"] is False
    assert _register(client, pk)["status"] == "success"  # the owner key registers the first passkey
    view = client.get("/state").json()["passkey"]
    assert view == {
        "registered": True,
        "credential_id": b64url(pk.credential_id),
        "signatures_used": 0,
        "owner_key_locked": False,
    }

    approval_id = _escalated_payment(client)
    first = client.post(f"/approvals/{approval_id}/approve")
    assert first.status_code == 428
    challenge = first.json()["passkey"]
    assert challenge["credential_id"] == b64url(pk.credential_id)
    assert challenge["summary"].startswith("Approve: Pay 6000 tHKD to landlord")
    assert challenge["call"]["function"] == "approveRequest"
    assert client.get("/state").json()["approvals"][0]["id"] == approval_id  # still pending

    out = client.post(f"/approvals/{approval_id}/approve", json={"signed": _signed(pk, challenge)}).json()
    assert out["status"] == "success"
    landlord = client.runtime.ctx.chain.deployment["payees"]["landlord"]
    assert client.runtime.ctx.chain.tokens["tHKD"].functions.balanceOf(landlord).call() == 6_000 * 10**6
    assert client.get("/passkey").json()["signatures_used"] == 1
    kinds = [h["kind"] for h in client.get("/history").json()["history"]]
    assert "passkey_signed" in kinds and "passkey_set" in kinds
    signed = next(h for h in client.get("/history").json()["history"] if h["kind"] == "passkey_signed")
    assert signed["action"] == "approveRequest"


@pytestmark_chain
def test_wrong_passkey_and_reused_challenge_are_refused(client):
    pk, stranger = SoftwarePasskey(), SoftwarePasskey()
    _register(client, pk)
    approval_id = _escalated_payment(client)
    challenge = client.post(f"/approvals/{approval_id}/approve").json()["passkey"]

    wrong = client.post(f"/approvals/{approval_id}/approve", json={"signed": _signed(stranger, challenge)})
    assert wrong.status_code == 400 and "not the one registered" in wrong.json()["detail"]
    # The challenge was used up by that attempt: a second try needs a fresh one.
    again = client.post(f"/approvals/{approval_id}/approve", json={"signed": _signed(pk, challenge)})
    assert again.status_code == 400 and "expired" in again.json()["detail"]

    # A challenge for one approval cannot approve another.
    other_id = _escalated_payment(client, 5_500)
    challenge = client.post(f"/approvals/{approval_id}/approve").json()["passkey"]
    swapped = client.post(f"/approvals/{other_id}/approve", json={"signed": _signed(pk, challenge)})
    assert swapped.status_code == 400
    assert len(client.get("/state").json()["approvals"]) == 2


@pytestmark_chain
def test_lock_owner_key_then_only_the_passkey_works(client):
    pk = SoftwarePasskey()
    chain = client.runtime.ctx.chain
    # Locking needs the passkey even before it is registered: the vault refuses the owner key.
    no_passkey = client.post("/passkey/lock", json={"locked": True}).json()
    assert no_passkey["status"] == "rejected" and "OnlyViaPasskey" in no_passkey["error"]

    _register(client, pk)
    assert _as_student(client, pk, "/passkey/lock", {"locked": True})["status"] == "success"
    assert client.get("/passkey").json()["owner_key_locked"] is True

    # The owner key on this computer has no power left.
    approval_id = _escalated_payment(client)
    request_id = client.runtime.ctx.store.approval(approval_id)["onchain_id"]
    direct = chain.approve_request(request_id)
    assert direct.status == "rejected" and direct.error == "NotOwner()"

    # Freezing never needs the other half; unfreezing now needs the passkey.
    assert client.post("/freeze", json={"by": "student"}).json()["status"] == "success"
    assert client.post("/unfreeze").status_code == 428
    assert _as_student(client, pk, "/unfreeze")["status"] == "success"
    assert client.get("/state").json()["status"]["frozen"] is False

    # And approvals go through the passkey.
    assert _as_student(client, pk, f"/approvals/{approval_id}/approve")["status"] == "success"


@pytestmark_chain
def test_replacing_the_passkey_needs_the_current_one(client):
    old, new = SoftwarePasskey(), SoftwarePasskey()
    _register(client, old)
    body = {"credential_id": b64url(new.credential_id), "public_key": b64url(new.spki())}
    challenge = client.post("/passkey", json=body).json()["passkey"]
    assert challenge["credential_id"] == b64url(old.credential_id)  # the current passkey must sign
    out = client.post("/passkey", json=body | {"signed": _signed(old, challenge)}).json()
    assert out["status"] == "success"
    assert client.get("/passkey").json()["credential_id"] == b64url(new.credential_id)
    # The old passkey no longer works; the new one does.
    challenge = client.post("/unfreeze").json()["passkey"]
    assert client.post("/unfreeze", json={"signed": _signed(old, challenge)}).status_code == 400


@pytestmark_chain
def test_reject_with_passkey_and_expired_signature(client):
    pk = SoftwarePasskey()
    _register(client, pk)
    approval_id = _escalated_payment(client)
    assert _as_student(client, pk, f"/approvals/{approval_id}/reject")["status"] == "rejected"

    other = _escalated_payment(client)
    challenge = client.post(f"/approvals/{other}/approve").json()["passkey"]
    client.runtime.ctx.chain.warp(1)  # the signature reaches the chain after its 5-minute deadline
    late = client.post(f"/approvals/{other}/approve", json={"signed": _signed(pk, challenge)}).json()
    assert late["status"] == "rejected" and late["error"].startswith("PasskeyExpired")
    assert client.get("/state").json()["approvals"][0]["id"] == other  # still waiting for the student


@pytestmark_chain
def test_remove_passkey_only_while_owner_key_unlocked(client):
    pk = SoftwarePasskey()
    _register(client, pk)
    _as_student(client, pk, "/passkey/lock", {"locked": True})
    refused = _as_student(client, pk, "/passkey/remove")
    assert refused["status"] == "rejected" and refused["error"] == "OwnerKeyLocked()"
    _as_student(client, pk, "/passkey/lock", {"locked": False})
    assert _as_student(client, pk, "/passkey/remove")["status"] == "success"
    assert client.get("/passkey").json()["registered"] is False
    assert "passkey_removed" in [h["kind"] for h in client.get("/history").json()["history"]]
    assert client.post("/unfreeze").status_code == 200  # the owner key signs again, no challenge


@pytestmark_chain
def test_bad_passkey_registration_is_refused(client):
    r = client.post("/passkey", json={"credential_id": "AAAA", "public_key": b64url(b"junk")})
    assert r.status_code == 400
    assert client.get("/passkey").json()["registered"] is False
