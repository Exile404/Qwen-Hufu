"""On-chain access: the vault, test stablecoins and FX pool, plus the three signers.

Signers mirror the tiger tally:
  agent    = the commander's half (session key; limited by the vault policy)
  owner    = the student's half: an owner key, or the student's passkey once one is registered
             (then the agent key only relays what the passkey signed; see student.py)
  guardian = a trusted contact who can only pause
"""

import copy
import functools
import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from eth_abi import decode as abi_decode
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexStr
from eth_utils import keccak, to_checksum_address
from web3 import Web3
from web3.exceptions import ContractCustomError, ContractLogicError
from web3.types import RPCEndpoint

from hufu.config import REPO_ROOT, Settings, get_settings
from hufu.passkey import b64url

DECIMALS = 6
UNIT = 10**DECIMALS
SYMBOLS = ("tHKD", "tUSD")
FORCED_GAS = 300_000
OUT_DIR = REPO_ROOT / "contracts" / "out"

# Plain-language meaning of each vault error, for the agent and the dashboard.
ERROR_HINTS = {
    "PayeeNotAllowed": "the recipient is not on the student's saved-payee list",
    "OverPerTxCap": "the amount is above the per-payment limit the student set",
    "OverPayeeCap": "this payee would receive more today than the daily limit the student set for them",
    "OverDailyCap": "this would exceed today's spending limit",
    "IsPaused": "the wallet is frozen; only the student can unfreeze it",
    "AgentExpired": "the agent's session key has expired; the student must renew it",
    "NotAgent": "only the agent key can do this",
    "NotOwner": "only the student (owner) can do this",
    "TokenNotEnabled": "this token is not enabled in the wallet",
    "MandateNotDue": "this recurring payment is not due yet",
    "MandateNotActive": "this recurring payment is not active",
    "MandateEnded": "this recurring payment has ended",
    "UnknownMandate": "no recurring payment with that id",
    "RequestNotPending": "that request was already decided",
    "UnknownRequest": "no request with that id",
    "ZeroAmount": "the amount must be greater than zero",
    "Slippage": "the conversion rate moved beyond the allowed slippage",
    "NoFxPool": "currency conversion is not set up",
    "ERC20InsufficientBalance": "the wallet does not hold enough of this token",
    "NoPasskey": "no passkey is registered for this wallet",
    "InvalidPasskey": "that is not a valid P-256 passkey public key",
    "PasskeyExpired": "the passkey signature expired before it reached the chain; sign again",
    "BadPasskeySignature": "the vault did not accept the passkey signature",
    "OnlyViaPasskey": "only the student's passkey can do this",
    "OwnerKeyLocked": "unlock the owner key first, so the wallet always keeps a working owner",
}
_AMOUNT_ERRORS = {"OverPerTxCap", "OverPayeeCap", "OverDailyCap", "Slippage", "ERC20InsufficientBalance"}


def to_units(amount: Any) -> int:
    """Token amount (e.g. 4500 or "4500.50") to integer base units. Rejects sub-unit precision."""
    try:
        d = Decimal(str(amount))
    except ArithmeticError:
        raise ValueError(f"not an amount: {amount!r}") from None
    if not d.is_finite():
        raise ValueError(f"not an amount: {amount!r}")
    if d <= 0:
        raise ValueError("amount must be greater than zero")
    exponent = d.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -DECIMALS:
        raise ValueError(f"at most {DECIMALS} decimal places")
    return int(d * UNIT)


def from_units(units: int) -> float:
    return float(Decimal(units) / UNIT)


def memo_hash(text: str) -> str:
    return "0x" + keccak(text=text).hex()


def lookalike(address: str, salt: str = "poison") -> str:
    """An address-poisoning lookalike: same first 6 and last 6 hex characters, different middle."""
    a = address.lower().removeprefix("0x")
    middle = keccak(text=f"{salt}:{a}").hex()[: 40 - 12]
    return to_checksum_address("0x" + a[:6] + middle + a[-6:])


def _load_abi(name: str) -> list[dict]:
    return json.loads((OUT_DIR / f"{name}.sol" / f"{name}.json").read_text())["abi"]


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M UTC")


@dataclass
class TxResult:
    status: str  # success | reverted | rejected (refused before sending, at gas estimation)
    tx_hash: str | None = None
    block: int | None = None
    error: str | None = None
    hint: str | None = None
    explorer: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def per_block[F: Callable[..., Any]](fn: F) -> F:
    """Cache a read for the current block. State only changes when a block is mined, and a public RPC
    node is slow: without this, every dashboard refresh repeats dozens of calls on Sepolia."""

    @functools.wraps(fn)
    def wrapper(self: "Chain", *args: Any, **kwargs: Any) -> Any:
        key = (fn.__name__, args, tuple(sorted(kwargs.items())))
        block = self.w3.eth.block_number
        hit = self._block_cache.get(key)
        if hit is None or hit[0] != block:
            hit = self._block_cache[key] = (block, fn(self, *args, **kwargs))
        return copy.deepcopy(hit[1])

    return cast(F, wrapper)


def _batch(w3: Web3, calls: list[Any]) -> list[Any]:
    """Send independent reads as one JSON-RPC batch: one round trip instead of one per call.
    Items are contract function calls, or zero-argument callables for `w3.eth` reads."""
    if not calls:
        return []
    if len(calls) == 1:  # some nodes (FISCO BCOS) answer a one-item batch with a bare object
        c = calls[0]
        return [c.call() if hasattr(c, "call") else c()]
    with w3.batch_requests() as batch:
        for c in calls:
            batch.add(c if hasattr(c, "call") else c())
        return list(batch.execute())


class ChainNotDeployed(RuntimeError):
    """The deployment file points at contracts that are not on the running chain."""


class Chain:
    def __init__(self, settings: Settings | None = None):
        s = self.settings = settings or get_settings()
        self.w3 = Web3(Web3.HTTPProvider(s.rpc_url, request_kwargs={"timeout": 30}))
        if not s.deployments_file.exists():
            raise FileNotFoundError(f"No deployment at {s.deployments_file}. Run: make deploy-local")
        self.deployment = json.loads(s.deployments_file.read_text())
        abis = {n: _load_abi(n) for n in ("HufuVault", "MockStablecoin", "MockFXPool")}
        d = self.deployment
        self.vault = self.w3.eth.contract(address=to_checksum_address(d["vault"]), abi=abis["HufuVault"])
        if not self.w3.eth.get_code(self.vault.address):
            raise ChainNotDeployed(
                f"No HufuVault at {self.vault.address} on {s.rpc_url}. The local chain was probably restarted "
                "(it starts empty). Run: make deploy-local"
            )
        self.pool = self.w3.eth.contract(address=to_checksum_address(d["fxPool"]), abi=abis["MockFXPool"])
        self.tokens = {
            sym: self.w3.eth.contract(address=to_checksum_address(d[sym]), abi=abis["MockStablecoin"])
            for sym in SYMBOLS
        }
        self._symbol_by_addr = {c.address.lower(): sym for sym, c in self.tokens.items()}
        self._seed_names: dict[str, str] = {to_checksum_address(a): n for n, a in d["payees"].items()}
        self._errors = _error_table(abis.values())
        self._selectors = {
            keccak(text=f"{f['name']}({','.join(i['type'] for i in f['inputs'])})")[:4]: f["name"]
            for f in abis["HufuVault"]
            if f.get("type") == "function"
        }
        self._block_cache: dict[Any, tuple[int, Any]] = {}

        self.agent: LocalAccount = Account.from_key(s.agent_private_key)
        self.owner: LocalAccount = Account.from_key(s.owner_private_key)
        self.guardian: LocalAccount | None = (
            Account.from_key(s.guardian_private_key) if s.guardian_private_key else None
        )

    # ------------------------------------------------------------------ helpers

    def token(self, symbol: str) -> Any:
        """Resolve 'tHKD', 'hkd', 'HKD' ... to the token contract."""
        key = symbol.strip().lower().removeprefix("t")
        for sym, c in self.tokens.items():
            if sym.lower().removeprefix("t") == key:
                return c
        raise ValueError(f"unknown token {symbol!r}; use one of {', '.join(SYMBOLS)}")

    def symbol(self, address: str) -> str:
        return self._symbol_by_addr.get(address.lower(), address)

    def explorer(self, tx_hash: str) -> str | None:
        base = self.settings.explorer_tx_url
        return f"{base}{tx_hash}" if base else None

    def now(self) -> int:
        return int(self.w3.eth.get_block("latest")["timestamp"])

    # ------------------------------------------------------------------ reads

    @per_block
    def payees(self) -> list[dict[str, Any]]:
        """Current allowlist, rebuilt from PayeeSet events (the chain is the source of truth)."""
        latest: dict[str, dict[str, Any]] = {}
        for ev in self.vault.events.PayeeSet().get_logs(
            from_block=self.deployment["deployBlock"], to_block="latest"
        ):
            payee = to_checksum_address(ev["args"]["payee"])
            latest[payee] = {"allowed": ev["args"]["allowed"], "label": ev["args"]["label"]}
        v = self.vault.functions
        pairs = [(addr, sym) for addr in latest for sym in SYMBOLS]
        day, *cap_list = _batch(
            self.w3, [v.today()] + [v.payeeCap(a, self.tokens[sym].address) for a, sym in pairs]
        )
        capped = [(pair, cap) for pair, cap in zip(pairs, cap_list, strict=True) if cap]
        paid = _batch(
            self.w3, [v.paidToPayeeOnDay(a, self.tokens[sym].address, day) for (a, sym), _ in capped]
        )
        caps: dict[str, dict[str, float]] = {a: {} for a in latest}
        left: dict[str, dict[str, float]] = {a: {} for a in latest}
        for ((addr, sym), cap), spent in zip(capped, paid, strict=True):
            caps[addr][sym], left[addr][sym] = from_units(cap), from_units(max(cap - spent, 0))
        return [
            {
                "name": self._seed_names.get(addr) or _slug(p["label"]),
                "address": addr,
                "label": p["label"],
                "allowed": p["allowed"],
                "caps": caps[addr],  # daily limit per payee
                "left_today": left[addr],
            }
            for addr, p in latest.items()
        ]

    def resolve_payee(self, ref: str) -> dict[str, Any]:
        """A saved payee by name/label, or a raw 0x address (which may not be allowed)."""
        ref = ref.strip()
        if re.fullmatch(r"0x[0-9a-fA-F]{40}", ref):
            addr = to_checksum_address(ref)
            for p in self.payees():
                if p["address"] == addr:
                    return p
            return {
                "name": None,
                "address": addr,
                "label": None,
                "allowed": False,
                "caps": {},
                "left_today": {},
            }
        key = ref.lower()
        for p in self.payees():
            if key in (p["name"].lower(), p["label"].lower()) or key in p["label"].lower():
                return p
        raise ValueError(f"no saved payee called {ref!r}")

    @per_block
    def status(self) -> dict[str, Any]:
        v = self.vault.functions
        toks = list(self.tokens.items())
        per_token = [
            call
            for _, c in toks
            for call in (
                v.tokenPolicy(c.address),
                c.functions.balanceOf(self.vault.address),
                v.remainingToday(c.address),
            )
        ]
        paused, active, expiry, block, *rest = _batch(
            self.w3,
            [
                v.paused(),
                v.agentActive(),
                v.agentExpiry(),
                lambda: self.w3.eth.get_block("latest"),
                *per_token,
            ],
        )
        tokens = {}
        for i, (sym, _) in enumerate(toks):
            (enabled, per_tx, daily), balance, left = rest[3 * i : 3 * i + 3]
            tokens[sym] = {
                "balance": from_units(balance),
                "per_payment_limit": from_units(per_tx),
                "daily_limit": from_units(daily),
                "left_today": from_units(left),
                "enabled": enabled,
            }
        return {
            "tokens": tokens,
            "frozen": paused,
            "agent_active": active,
            "agent_session_expires": _iso(expiry),
            "chain_time": _iso(block["timestamp"]),
        }

    @per_block
    def mandates(self) -> list[dict[str, Any]]:
        names = {p["address"]: p["name"] for p in self.payees()}
        now = self.now()
        out = []
        for i, (payee, token, amount, interval, next_due, end, active, label) in enumerate(
            self._mandate_rows()
        ):
            ended = end != 0 and next_due > end
            out.append(
                {
                    "id": i,
                    "label": label,
                    "payee": names.get(to_checksum_address(payee), payee),
                    "token": self.symbol(token),
                    "amount": from_units(amount),
                    "every_days": interval // 86400,
                    "next_due": _iso(next_due),
                    "ends": _iso(end) if end else "open-ended",
                    "active": active and not ended,
                    "due_now": active and not ended and next_due <= now,
                }
            )
        return out

    @per_block
    def mandate_schedule(self) -> list[dict[str, Any]]:
        """The active recurring payments with raw unix times, for planning ahead."""
        names = {p["address"]: p["name"] for p in self.payees()}
        return [
            {
                "id": i,
                "label": label,
                "payee": names.get(to_checksum_address(payee), payee),
                "token": self.symbol(token),
                "amount": from_units(amount),
                "interval": interval,
                "next_due": next_due,
                "end": end,
            }
            for i, (payee, token, amount, interval, next_due, end, active, label) in enumerate(
                self._mandate_rows()
            )
            if active
        ]

    @per_block
    def _mandate_rows(self) -> list[tuple[Any, ...]]:
        v = self.vault.functions
        count = v.mandateCount().call()
        return [tuple(m) for m in _batch(self.w3, [v.getMandate(i) for i in range(count)])]

    def deployment_marker(self) -> str:
        """Identifies this deployment even when a fresh chain reuses the same vault address."""
        block = self.deployment["deployBlock"]
        return f"{self.settings.chain_id}:{block}:{self.w3.eth.get_block(block)['hash'].hex()}"

    def block_number(self) -> int:
        return self.w3.eth.block_number

    def outflows(self, from_block: int, to_block: int) -> list[dict[str, Any]]:
        """Money that left the vault through the agent key in blocks `from_block`..`to_block`."""
        names = {p["address"]: p["name"] for p in self.payees()}

        def who(addr: str) -> str:
            return names.get(to_checksum_address(addr), to_checksum_address(addr))

        def amount(units: int, token: str) -> str:
            return f"{from_units(units):g} {self.symbol(token)}"

        out = []
        e = self.vault.events
        for ev in e.Paid().get_logs(from_block=from_block, to_block=to_block):
            a = ev["args"]
            out.append((ev, f"paid {amount(a['amount'], a['token'])} to {who(a['to'])}"))
        for ev in e.MandateExecuted().get_logs(from_block=from_block, to_block=to_block):
            a = ev["args"]
            out.append(
                (
                    ev,
                    f"ran recurring payment #{a['id']}: {amount(a['amount'], a['token'])} to {who(a['payee'])}",
                )
            )
        for ev in e.Swapped().get_logs(from_block=from_block, to_block=to_block):
            a = ev["args"]
            out.append(
                (
                    ev,
                    f"converted {amount(a['amountIn'], a['tokenIn'])} to {amount(a['amountOut'], a['tokenOut'])}",
                )
            )
        return [
            {
                "what": what,
                "tx_hash": "0x" + ev["transactionHash"].hex().removeprefix("0x"),
                "block": ev["blockNumber"],
            }
            for ev, what in sorted(out, key=lambda x: (x[0]["blockNumber"], x[0]["logIndex"]))
        ]

    @per_block
    def passkey(self) -> dict[str, Any]:
        """The student's passkey as the vault knows it: public key, signatures used, owner key locked."""
        v = self.vault.functions
        x, y, nonce, retired = _batch(
            self.w3, [v.passkeyX(), v.passkeyY(), v.passkeyNonce(), v.ownerKeyRetired()]
        )
        x, y = int.from_bytes(x, "big"), int.from_bytes(y, "big")
        credential_id = None
        if x or y:
            logs = self.vault.events.PasskeySet().get_logs(
                from_block=self.deployment["deployBlock"], to_block="latest"
            )
            credential_id = b64url(logs[-1]["args"]["credentialId"]) if logs else None
        return {
            "registered": bool(x or y),
            "x": x,
            "y": y,
            "credential_id": credential_id,
            "signatures_used": nonce,
            "owner_key_locked": retired,
        }

    def requests(self) -> list[dict[str, Any]]:
        status_names = ["none", "pending", "approved", "rejected"]
        out = []
        for i in range(self.vault.functions.requestCount().call()):
            token, to, amount, mhash, created, status = self.vault.functions.getRequest(i).call()
            out.append(
                {
                    "id": i,
                    "token": self.symbol(token),
                    "to": to_checksum_address(to),
                    "amount": from_units(amount),
                    "memo_hash": "0x" + mhash.hex(),
                    "created": _iso(created),
                    "status": status_names[status],
                }
            )
        return out

    @per_block
    def history(self, limit: int = 10, memo_lookup: Any = None) -> list[dict[str, Any]]:
        """Recent vault activity, newest first, from events."""
        names = {p["address"]: p["name"] for p in self.payees()}
        start = self.deployment["deployBlock"]
        rows: list[tuple[int, int, dict[str, Any]]] = []

        def add(ev: Any, item: dict[str, Any]) -> None:
            item["tx_hash"] = "0x" + ev["transactionHash"].hex()
            rows.append((ev["blockNumber"], ev["logIndex"], item))

        def who(addr: str) -> str:
            return names.get(to_checksum_address(addr), to_checksum_address(addr))

        def memo(h: bytes) -> str | None:
            return memo_lookup("0x" + h.hex()) if memo_lookup else None

        e = self.vault.events
        for ev in e.Paid().get_logs(from_block=start, to_block="latest"):
            a = ev["args"]
            add(
                ev,
                {
                    "kind": "payment",
                    "to": who(a["to"]),
                    "amount": from_units(a["amount"]),
                    "token": self.symbol(a["token"]),
                    "memo": memo(a["memoHash"]),
                },
            )
        for ev in e.MandateExecuted().get_logs(from_block=start, to_block="latest"):
            a = ev["args"]
            add(
                ev,
                {
                    "kind": "mandate_payment",
                    "mandate_id": a["id"],
                    "to": who(a["payee"]),
                    "amount": from_units(a["amount"]),
                    "token": self.symbol(a["token"]),
                },
            )
        for ev in e.Swapped().get_logs(from_block=start, to_block="latest"):
            a = ev["args"]
            add(
                ev,
                {
                    "kind": "conversion",
                    "from": f"{from_units(a['amountIn'])} {self.symbol(a['tokenIn'])}",
                    "to": f"{round(from_units(a['amountOut']), 2)} {self.symbol(a['tokenOut'])}",
                },
            )
        for ev in e.PaymentRequested().get_logs(from_block=start, to_block="latest"):
            a = ev["args"]
            add(
                ev,
                {
                    "kind": "approval_requested",
                    "request_id": a["id"],
                    "to": who(a["to"]),
                    "amount": from_units(a["amount"]),
                    "token": self.symbol(a["token"]),
                    "memo": memo(a["memoHash"]),
                },
            )
        for ev in e.RequestApproved().get_logs(from_block=start, to_block="latest"):
            add(ev, {"kind": "request_approved", "request_id": ev["args"]["id"]})
        for ev in e.RequestRejected().get_logs(from_block=start, to_block="latest"):
            add(ev, {"kind": "request_rejected", "request_id": ev["args"]["id"]})
        for ev in e.MandateCreated().get_logs(from_block=start, to_block="latest"):
            a = ev["args"]
            add(ev, {"kind": "mandate_created", "mandate_id": a["id"], "label": a["label"]})
        for ev in e.PasskeySet().get_logs(from_block=start, to_block="latest"):
            add(ev, {"kind": "passkey_set" if any(ev["args"]["x"]) else "passkey_removed"})
        for ev in e.PasskeyUsed().get_logs(from_block=start, to_block="latest"):
            a = ev["args"]
            add(
                ev,
                {
                    "kind": "passkey_signed",
                    "action": self._selectors.get(a["action"], "0x" + a["action"].hex()),
                },
            )
        for ev in e.OwnerKeyRetired().get_logs(from_block=start, to_block="latest"):
            add(ev, {"kind": "owner_key_locked" if ev["args"]["retired"] else "owner_key_unlocked"})
        for ev in e.Paused().get_logs(from_block=start, to_block="latest"):
            add(ev, {"kind": "frozen", "by": ev["args"]["by"]})
        for ev in e.Unpaused().get_logs(from_block=start, to_block="latest"):
            add(ev, {"kind": "unfrozen", "by": ev["args"]["by"]})

        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        return [{k: v for k, v in r[2].items() if v is not None} for r in rows[:limit]]

    def quote(self, token_in: str, amount: Any) -> dict[str, Any]:
        c = self.token(token_in)
        out = self.pool.functions.quote(c.address, to_units(amount)).call()
        rate = self.pool.functions.hkdPerUsd().call() / 1e6
        fee = self.pool.functions.feeBps().call() / 100
        out_sym = self.symbol(self.pool.functions.tokenOutFor(c.address).call())
        return {
            "pay": f"{amount} {self.symbol(c.address)}",
            "receive": round(from_units(out), 2),
            "receive_token": out_sym,
            "rate_hkd_per_usd": rate,
            "fee_percent": fee,
            "source": "sample rate (test FX pool)",
        }

    def usd_needed_for_hkd(self, hkd: float) -> float:
        """tUSD to convert so that at least `hkd` tHKD arrives, after the pool fee. Rounded up to cents."""
        rate = self.pool.functions.hkdPerUsd().call() / 1e6
        fee = self.pool.functions.feeBps().call() / 10_000
        return math.ceil(hkd / (rate * (1 - fee)) * 100) / 100

    # ------------------------------------------------------------------ agent (commander's half)

    def pay(
        self, token: str, to: str, amount_units: int, memo_hash_hex: str, force: bool = False
    ) -> TxResult:
        fn = self.vault.functions.pay(
            self.token(token).address, to_checksum_address(to), amount_units, memo_hash_hex
        )
        return self.send(fn, self.agent, force=force)

    def request_payment(
        self, token: str, to: str, amount_units: int, memo_hash_hex: str
    ) -> tuple[TxResult, int | None]:
        fn = self.vault.functions.requestPayment(
            self.token(token).address, to_checksum_address(to), amount_units, memo_hash_hex
        )
        res = self.send(fn, self.agent)
        request_id = None
        if res.ok and res.tx_hash:
            receipt = self.w3.eth.get_transaction_receipt(HexStr(res.tx_hash))
            request_id = self.vault.events.PaymentRequested().process_receipt(receipt)[0]["args"]["id"]
        return res, request_id

    def execute_mandate(self, mandate_id: int) -> TxResult:
        return self.send(self.vault.functions.executeMandate(mandate_id), self.agent)

    def swap(self, token_in: str, amount_units: int, slippage_bps: int = 50) -> TxResult:
        c = self.token(token_in)
        quoted = self.pool.functions.quote(c.address, amount_units).call()
        min_out = quoted * (10_000 - slippage_bps) // 10_000
        return self.send(self.vault.functions.swap(c.address, amount_units, min_out), self.agent)

    def pause(self, signer: LocalAccount | None = None) -> TxResult:
        return self.send(self.vault.functions.pause(), signer or self.agent)

    def anchor_audit(self, head_hex: str) -> TxResult:
        return self.send(self.vault.functions.anchorAudit(head_hex), self.agent)

    # ------------------------------------------------------------------ owner (student's half)

    def approve_request(self, request_id: int) -> TxResult:
        return self.send(self.vault.functions.approveRequest(request_id), self.owner)

    def reject_request(self, request_id: int) -> TxResult:
        return self.send(self.vault.functions.rejectRequest(request_id), self.owner)

    def create_mandate(
        self, payee: str, token: str, amount_units: int, interval_s: int, first_due: int, end: int, label: str
    ) -> TxResult:
        fn = self.vault.functions.createMandate(
            to_checksum_address(payee),
            self.token(token).address,
            amount_units,
            interval_s,
            first_due,
            end,
            label,
        )
        return self.send(fn, self.owner)

    def set_payee(self, address: str, allowed: bool, label: str) -> TxResult:
        return self.send(
            self.vault.functions.setPayee(to_checksum_address(address), allowed, label), self.owner
        )

    def unpause(self) -> TxResult:
        return self.send(self.vault.functions.unpause(), self.owner)

    # ------------------------------------------------------------------ the student's passkey

    def owner_call(self, name: str, *args: Any) -> tuple[Any, bytes]:
        """An owner function call, and its calldata (what a passkey signs)."""
        fn = getattr(self.vault.functions, name)(*args)
        return fn, bytes.fromhex(self.vault.encode_abi(name, args=list(args)).removeprefix("0x"))

    def passkey_challenge(self, data: bytes, valid_until: int) -> bytes:
        """The 32 bytes the passkey signs for `data`: bound to this vault, chain, next nonce and deadline."""
        v = self.vault.functions
        return bytes(v.passkeyDigest(data, v.passkeyNonce().call(), valid_until).call())

    def send_with_passkey(self, data: bytes, valid_until: int, auth: tuple[Any, ...]) -> TxResult:
        """Relay a passkey-signed owner action. The agent key pays the gas; the vault checks the signature,
        so the relayer cannot change what the student signed."""
        return self.send(self.vault.functions.executeWithPasskey(data, valid_until, auth), self.agent)

    # ------------------------------------------------------------------ dev only (local anvil)

    def warp(self, days: float) -> int:
        if not 31337 <= self.w3.eth.chain_id <= 31999:
            raise RuntimeError("time travel is only available on a local chain")
        self.w3.provider.make_request(RPCEndpoint("evm_increaseTime"), [int(days * 86400)])
        self.w3.provider.make_request(RPCEndpoint("evm_mine"), [])
        return self.now()

    # ------------------------------------------------------------------ sending

    def send(self, fn: Any, signer: LocalAccount, force: bool = False) -> TxResult:
        """Sign and send. Normally a revert is caught at gas estimation and nothing is sent
        ('rejected'). With force=True the gas limit is fixed so a doomed transaction still lands
        on-chain as 'reverted' (used to produce verifiable evidence for the jailbreak demo)."""
        params: dict[str, Any] = {
            "from": signer.address,
            "nonce": self.w3.eth.get_transaction_count(signer.address, "pending"),
            "chainId": self.w3.eth.chain_id,
        }
        if force:
            params["gas"] = FORCED_GAS
        try:
            tx = fn.build_transaction(params)
        except (ContractCustomError, ContractLogicError) as exc:
            name, text = self.decode_error(exc)
            return TxResult("rejected", error=text, hint=ERROR_HINTS.get(name))
        signed = signer.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        h = "0x" + receipt["transactionHash"].hex().removeprefix("0x")
        if receipt["status"] == 1:
            return TxResult("success", tx_hash=h, block=receipt["blockNumber"], explorer=self.explorer(h))
        name, text = self._replay_error(fn, signer, receipt["blockNumber"])
        return TxResult(
            "reverted",
            tx_hash=h,
            block=receipt["blockNumber"],
            error=text,
            hint=ERROR_HINTS.get(name),
            explorer=self.explorer(h),
        )

    def _replay_error(self, fn: Any, signer: LocalAccount, block: int) -> tuple[str, str]:
        try:
            fn.call({"from": signer.address}, block_identifier=block - 1)
        except (ContractCustomError, ContractLogicError) as exc:
            return self.decode_error(exc)
        return "", "reverted"

    def decode_error(self, exc: Exception) -> tuple[str, str]:
        data = getattr(exc, "data", None)
        if isinstance(data, str) and data.startswith("0x") and len(data) >= 10:
            entry = self._errors.get(data[:10].lower())
            if entry:
                name, types = entry
                args = abi_decode(types, bytes.fromhex(data[10:])) if types else ()
                shown = [from_units(a) if name in _AMOUNT_ERRORS and isinstance(a, int) else a for a in args]
                return name, f"{name}({', '.join(map(str, shown))})"
        return "", str(exc)


def _error_table(abis: Any) -> dict[str, tuple[str, list[str]]]:
    table = {}
    for abi in abis:
        for item in abi:
            if item.get("type") == "error":
                types = [i["type"] for i in item["inputs"]]
                sig = f"{item['name']}({','.join(types)})"
                table["0x" + keccak(text=sig).hex()[:8]] = (item["name"], types)
    return table


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24] or "payee"
