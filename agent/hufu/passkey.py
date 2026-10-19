"""The student's half as a passkey (WebAuthn, P-256), checked by the vault on-chain.

The dashboard asks the student's passkey (phone, laptop or security key; fingerprint, face or PIN) to sign
the vault's challenge for one owner action. This module turns the browser's WebAuthn assertion into the
vault's `WebAuthnAuth` struct, checks it off-chain first so a wrong passkey gets a clear message instead of
a failed transaction, and provides a software passkey that produces the same format for tests and the
scripted on-chain evidence.
"""

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
from Crypto.Signature import DSS

P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
FLAG_UP, FLAG_UV = 0x01, 0x04  # authenticator data flags: user present, user verified


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise ValueError("not base64url") from None


def public_key_from_spki(spki: bytes) -> tuple[int, int]:
    """(x, y) of a P-256 public key in the SubjectPublicKeyInfo form browsers return from getPublicKey()."""
    try:
        key = ECC.import_key(spki)
    except (ValueError, IndexError, TypeError):
        raise ValueError("not a public key") from None
    if key.curve != "NIST P-256" or key.has_private():
        raise ValueError("the passkey must be a P-256 (ES256) public key")
    return int(key.pointQ.x), int(key.pointQ.y)


def der_to_rs(sig: bytes) -> tuple[int, int]:
    """The (r, s) integers of a DER-encoded ECDSA signature, as WebAuthn returns it."""

    def integer(buf: bytes, i: int) -> tuple[int, int]:
        if buf[i] != 0x02 or buf[i + 1] > 33:
            raise ValueError("bad DER integer")
        n = buf[i + 1]
        return int.from_bytes(buf[i + 2 : i + 2 + n], "big"), i + 2 + n

    try:
        if sig[0] != 0x30 or sig[1] != len(sig) - 2:
            raise ValueError
        r, i = integer(sig, 2)
        s, i = integer(sig, i)
    except (IndexError, ValueError):
        raise ValueError("not a DER ECDSA signature") from None
    if i != len(sig) or not (0 < r < P256_N and 0 < s < P256_N):
        raise ValueError("not a DER ECDSA signature")
    return r, s


@dataclass
class Assertion:
    """What `navigator.credentials.get()` returns, decoded."""

    authenticator_data: bytes
    client_data_json: bytes
    signature: bytes  # DER
    credential_id: bytes = b""

    @classmethod
    def from_browser(cls, d: dict[str, Any]) -> "Assertion":
        try:
            return cls(
                b64url_decode(d["authenticator_data"]),
                b64url_decode(d["client_data_json"]),
                b64url_decode(d["signature"]),
                b64url_decode(d.get("credential_id") or ""),
            )
        except (KeyError, TypeError):
            raise ValueError("incomplete passkey response") from None

    def to_browser(self) -> dict[str, str]:
        return {
            "authenticator_data": b64url(self.authenticator_data),
            "client_data_json": b64url(self.client_data_json),
            "signature": b64url(self.signature),
            "credential_id": b64url(self.credential_id),
        }


def to_vault_auth(a: Assertion, challenge: bytes) -> tuple[bytes, bytes, int, int, bytes, str]:
    """The vault's WebAuthnAuth struct (r, s, challengeIndex, typeIndex, authenticatorData, clientDataJSON).
    Raises ValueError, in plain language, if this is not a signature of `challenge` by a verified user."""
    try:
        client = json.loads(a.client_data_json)
        text = a.client_data_json.decode()
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("the passkey response is not valid WebAuthn client data") from None
    if not isinstance(client, dict) or client.get("type") != "webauthn.get":
        raise ValueError("the passkey response is not a WebAuthn sign-in")
    expected = b64url(challenge)
    if client.get("challenge") != expected:
        raise ValueError("the passkey signed a different challenge")
    if len(a.authenticator_data) < 37:
        raise ValueError("the passkey response is too short")
    flags = a.authenticator_data[32]
    if not flags & FLAG_UP or not flags & FLAG_UV:
        raise ValueError("the passkey did not verify you (fingerprint, face or PIN is required)")
    # The vault finds both fields by byte offset in the exact JSON the authenticator signed.
    raw = a.client_data_json
    type_index = raw.find(b'"type":"webauthn.get"')
    challenge_index = raw.find(f'"challenge":"{expected}"'.encode())
    if type_index < 0 or challenge_index < 0:
        raise ValueError("unexpected WebAuthn client data layout")
    r, s = der_to_rs(a.signature)
    if s > P256_N // 2:  # the vault accepts only the low-s form; both forms are the same signature
        s = P256_N - s
    return (
        r.to_bytes(32, "big"),
        s.to_bytes(32, "big"),
        challenge_index,
        type_index,
        a.authenticator_data,
        text,
    )


def verify(a: Assertion, x: int, y: int) -> bool:
    """Off-chain check of the P-256 signature against the registered key (the vault checks it again)."""
    try:
        key = ECC.construct(curve="P-256", point_x=x, point_y=y)
        digest = SHA256.new(a.authenticator_data + hashlib.sha256(a.client_data_json).digest())
        DSS.new(key, "fips-186-3", encoding="der").verify(digest, a.signature)
    except ValueError:
        return False
    return True


class SoftwarePasskey:
    """A passkey held in software. It produces exactly what a phone or laptop passkey returns to the
    browser, so tests and the scripted Sepolia evidence go through the same on-chain check. The dashboard
    uses the student's real passkey."""

    def __init__(self, rp_id: str = "localhost", origin: str = "http://localhost:3000") -> None:
        self.key = ECC.generate(curve="P-256")
        self.credential_id = os.urandom(16)
        self.rp_id, self.origin = rp_id, origin
        self.sign_count = 0

    @property
    def public_key(self) -> tuple[int, int]:
        return int(self.key.pointQ.x), int(self.key.pointQ.y)

    def spki(self) -> bytes:
        return self.key.public_key().export_key(format="DER")

    def sign(self, challenge: bytes, user_verified: bool = True) -> Assertion:
        self.sign_count += 1
        flags = FLAG_UP | (FLAG_UV if user_verified else 0)
        auth_data = (
            hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + self.sign_count.to_bytes(4)
        )
        client = {"type": "webauthn.get", "challenge": b64url(challenge), "origin": self.origin}
        client_json = json.dumps(client | {"crossOrigin": False}, separators=(",", ":")).encode()
        digest = SHA256.new(auth_data + hashlib.sha256(client_json).digest())
        sig = DSS.new(self.key, "fips-186-3", encoding="der").sign(digest)
        return Assertion(auth_data, client_json, sig, self.credential_id)
