"""The student's half of the tally: every owner action goes through here.

Without a passkey the owner key signs, as in the original demo. Once the student registers a passkey on
the vault, each action becomes a challenge for that passkey instead: the first call raises
`PasskeyRequired` with the challenge, the dashboard has the passkey sign it (fingerprint, face or PIN on the
student's device), and the second call relays the signature. The vault checks it on-chain, so neither this
process nor the agent key can approve anything on the student's behalf. After the passkey has signed once,
the student can lock the owner key and it loses all power.
"""

import secrets
import time
from dataclasses import dataclass
from typing import Any

from hufu import passkey
from hufu.chain import TxResult
from hufu.tools import Context

CHALLENGE_TTL_S = 300  # the signature must reach the chain within 5 minutes
# Vault errors that mean "sign again", not "this action failed": the decision stays with the student.
RETRYABLE = ("PasskeyExpired", "BadPasskeySignature")


@dataclass
class Prepared:
    action: str  # what this challenge is for, e.g. "approve:3"; a signature only works for that action
    data: bytes  # the exact vault call the passkey signs
    valid_until: int
    challenge: bytes
    summary: str
    issued: float


class PasskeyRequired(Exception):
    """The vault has a passkey: the student signs `challenge` on their device and the call is repeated."""

    def __init__(self, challenge: dict[str, Any]) -> None:
        super().__init__("passkey signature required")
        self.challenge = challenge


def act(
    ctx: Context, action: str, summary: str, fn_name: str, *args: Any, signed: dict[str, Any] | None = None
) -> TxResult:
    """Run one owner action: `fn_name(*args)` on the vault, signed by the owner key or the passkey."""
    chain = ctx.chain
    fn, data = chain.owner_call(fn_name, *args)
    pk = chain.passkey()
    if not pk["registered"]:
        return chain.send(fn, chain.owner)

    if signed is None:
        valid_until = chain.now() + CHALLENGE_TTL_S
        challenge = chain.passkey_challenge(data, valid_until)
        _forget_expired(ctx)
        challenge_id = secrets.token_urlsafe(16)
        ctx.passkey_pending[challenge_id] = Prepared(
            action, data, valid_until, challenge, summary, time.monotonic()
        )
        raise PasskeyRequired(
            {
                "challenge_id": challenge_id,
                "challenge": passkey.b64url(challenge),
                "credential_id": pk["credential_id"],
                "valid_until": valid_until,
                "summary": summary,
                "call": {"function": fn_name, "args": [_show(a) for a in args]},
            }
        )

    p = ctx.passkey_pending.pop(str(signed.get("challenge_id")), None)
    if p is None or p.action != action:
        raise ValueError("this passkey request has expired or was for something else; try again")
    assertion = passkey.Assertion.from_browser(signed.get("assertion") or {})
    auth = passkey.to_vault_auth(assertion, p.challenge)
    if not passkey.verify(assertion, pk["x"], pk["y"]):
        raise ValueError("this passkey is not the one registered for this wallet")
    return chain.send_with_passkey(p.data, p.valid_until, auth)


def register(
    ctx: Context, credential_id: bytes, public_key_spki: bytes, signed: dict[str, Any] | None = None
) -> TxResult:
    """Register the student's passkey (owner key), or replace it (signed by the current passkey)."""
    x, y = passkey.public_key_from_spki(public_key_spki)
    if not 0 < len(credential_id) <= 1023:
        raise ValueError("bad passkey credential id")
    return act(
        ctx,
        f"set_passkey:{x:064x}",
        "Register this passkey as your half of the tally",
        "setPasskey",
        x.to_bytes(32, "big"),
        y.to_bytes(32, "big"),
        credential_id,
        signed=signed,
    )


def lock_owner_key(ctx: Context, locked: bool, signed: dict[str, Any] | None = None) -> TxResult:
    """Lock (or unlock) the owner key. The vault only accepts this from the passkey."""
    summary = (
        "Lock the owner key: only your passkey can approve from now on" if locked else "Unlock the owner key"
    )
    return act(ctx, f"owner_key_locked:{locked}", summary, "setOwnerKeyRetired", locked, signed=signed)


def remove_passkey(ctx: Context, signed: dict[str, Any] | None = None) -> TxResult:
    """Go back to the owner key alone (the vault refuses while the owner key is locked)."""
    return act(
        ctx,
        "remove_passkey",
        "Remove your passkey: the owner key signs again",
        "removePasskey",
        signed=signed,
    )


def unfreeze(ctx: Context, signed: dict[str, Any] | None = None) -> TxResult:
    return act(ctx, "unfreeze", "Unfreeze the wallet", "unpause", signed=signed)


def _forget_expired(ctx: Context) -> None:
    cutoff = time.monotonic() - CHALLENGE_TTL_S
    for k in [k for k, p in ctx.passkey_pending.items() if p.issued < cutoff]:
        del ctx.passkey_pending[k]


def _show(arg: Any) -> Any:
    return "0x" + arg.hex() if isinstance(arg, bytes) else arg
