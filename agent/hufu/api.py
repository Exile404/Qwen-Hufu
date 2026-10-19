"""HTTP API consumed by the dashboard.

One local runtime (chain client, store, guard, agent, autopilot) is shared by all requests and rebuilt
automatically when the contracts are redeployed. A lock serialises anything that signs, including the
autopilot's background runs.
"""

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from web3 import Web3

from hufu import __version__, approvals, huitong, llm, planner, seed
from hufu.agent import Agent, Turn
from hufu.autopilot import Autopilot
from hufu.chain import Chain, ChainNotDeployed, to_units
from hufu.config import get_settings
from hufu.guard import Guard
from hufu.guard.scanner import scan
from hufu.store import Store
from hufu.tools import Context

log = logging.getLogger("hufu.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    stop = threading.Event()
    worker = threading.Thread(target=_autopilot_loop, args=(stop,), name="autopilot", daemon=True)
    worker.start()
    yield
    stop.set()


app = FastAPI(title="Hufu API", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().web_origin, "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Runtime:
    def __init__(self) -> None:
        s = get_settings()
        self.chain = Chain(s)
        self.ctx = Context(self.chain, Store(s.db_path, self.chain.vault.address))
        self.guard = Guard(self.ctx)
        self.agent = Agent(self.ctx, guard=self.guard)
        self.autopilot = Autopilot(self.ctx, self.guard, s.autopilot, s.autopilot_interval_s)
        self.deployment_mtime = s.deployments_file.stat().st_mtime


_runtime: Runtime | None = None
_lock = threading.RLock()


def rt() -> Runtime:
    """The shared runtime; rebuilt if the deployment file changed (e.g. after `make deploy-local`)."""
    global _runtime
    s = get_settings()
    stale = _runtime is None or (
        s.deployments_file.exists() and s.deployments_file.stat().st_mtime != _runtime.deployment_mtime
    )
    if stale:
        try:
            _runtime = Runtime()
        except (FileNotFoundError, ChainNotDeployed) as e:
            raise HTTPException(503, str(e)) from e
        except Exception as e:
            raise HTTPException(503, f"Chain not reachable: {type(e).__name__}: {e}") from e
    return _runtime  # type: ignore[return-value]


def _autopilot_loop(stop: threading.Event) -> None:
    """Run the autopilot every interval while it is switched on. Never builds the runtime itself."""
    while not stop.wait(get_settings().autopilot_interval_s):
        r = _runtime
        if r is None or not r.autopilot.enabled:
            continue
        try:
            with _lock:
                r.autopilot.tick()
        except Exception:
            log.exception("autopilot run failed")


def chain_status() -> dict:
    s = get_settings()
    try:
        w3 = Web3(Web3.HTTPProvider(s.rpc_url, request_kwargs={"timeout": 3}))
        return {"ok": w3.is_connected(), "chain_id": w3.eth.chain_id, "block": w3.eth.block_number}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def turn_view(turn: Turn) -> dict[str, Any]:
    return {
        "reply": turn.reply,
        "verifier": turn.verifier,
        "anchor": turn.anchor,
        "steps": [asdict(s) for s in turn.steps],
    }


# ---------------------------------------------------------------- read


@app.get("/health")
def health() -> dict:
    chain = chain_status()
    model = llm.probe()
    return {"ok": chain["ok"] and model["ok"], "version": __version__, "chain": chain, "llm": model}


@app.get("/state")
def state() -> dict:
    r = rt()
    return {
        "vault": r.chain.vault.address,
        "chain_id": r.chain.settings.chain_id,
        "explorer_tx_url": r.chain.settings.explorer_tx_url or None,
        "status": r.chain.status(),
        "payees": r.chain.payees(),
        "mandates": r.chain.mandates(),
        "approvals": r.ctx.store.approvals("pending"),
        "guard": {"enabled": r.guard.enabled},
        "roles": {
            "owner": r.chain.owner.address,
            "agent": r.chain.agent.address,
            "guardian": r.chain.guardian.address if r.chain.guardian else None,
        },
    }


@app.get("/history")
def history(limit: int = 20) -> dict:
    r = rt()
    return {"history": r.chain.history(limit, r.ctx.store.memo)}


@app.get("/approvals")
def list_approvals() -> dict:
    r = rt()
    return {"approvals": list(reversed(r.ctx.store.approvals()))[:30]}


@app.get("/inbox")
def inbox() -> dict:
    """The inbox as the student sees it, with the scanner's rating per email."""
    r = rt()
    saved = {p["name"]: p["address"] for p in r.chain.payees() if p["allowed"]}
    emails = seed.inbox(r.chain.deployment["payees"]) + r.ctx.store.lab_emails()
    for e in emails:
        e["security"] = scan(f"{e['from']}\n{e['subject']}\n{e['body']}", saved).summary()
    return {"emails": emails}


@app.get("/huitong")
def huitong_quote(direction: str = "family_to_me", amount: float = 500, currency: str | None = "USD") -> dict:
    try:
        return huitong.compare(direction, amount, currency)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/audit")
def audit(limit: int = 30) -> dict:
    r = rt()
    anchors = r.chain.vault.events.AuditAnchored().get_logs(from_block=r.chain.deployment["deployBlock"])
    last = anchors[-1] if anchors else None
    head = "0x" + last["args"]["head"].hex() if last else None
    return {
        "entries": r.guard.audit.recent(limit),
        "verify": r.guard.audit.verify(),
        "anchors": len(anchors),
        "last_anchor": {
            "head": head,
            "tx_hash": "0x" + last["transactionHash"].hex().removeprefix("0x"),
            "in_local_log": head is not None and r.guard.audit.contains(head),
        }
        if last
        else None,
    }


@app.get("/plan")
def plan(days: int = 30) -> dict:
    return planner.plan(rt().chain, min(max(days, 1), 90))


# ---------------------------------------------------------------- the autopilot


@app.get("/autopilot")
def autopilot_status() -> dict:
    r = rt()
    return {**r.autopilot.status(), "plan": planner.plan(r.chain, 30)}


class AutopilotIn(BaseModel):
    enabled: bool


@app.post("/autopilot")
def autopilot_switch(body: AutopilotIn) -> dict:
    r = rt()
    with _lock:
        r.autopilot.set_enabled(body.enabled)
    return r.autopilot.status()


@app.post("/autopilot/run")
def autopilot_run() -> dict:
    """Run the autopilot once now (it also runs on its own every interval while switched on)."""
    with _lock:
        return rt().autopilot.tick()


# ---------------------------------------------------------------- the agent


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.post("/chat")
def chat(body: ChatIn) -> dict:
    with _lock:
        return turn_view(rt().agent.chat(body.text))


@app.post("/chat/reset")
def chat_reset() -> dict:
    with _lock:
        rt().agent.reset()
    return {"ok": True}


# ---------------------------------------------------------------- the student's half


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: int) -> dict:
    with _lock:
        try:
            return approvals.approve(rt().ctx, approval_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e


@app.post("/approvals/{approval_id}/reject")
def reject(approval_id: int) -> dict:
    with _lock:
        try:
            return approvals.reject(rt().ctx, approval_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e


class FreezeIn(BaseModel):
    by: str = Field("student", pattern="^(student|guardian|agent)$")


@app.post("/freeze")
def freeze(body: FreezeIn) -> dict:
    r = rt()
    signer = {"student": r.chain.owner, "guardian": r.chain.guardian, "agent": r.chain.agent}[body.by]
    with _lock:
        res = r.chain.pause(signer)
        r.guard.audit.append("frozen", {"by": body.by, **res.as_dict()})
        return res.as_dict()


@app.post("/unfreeze")
def unfreeze() -> dict:
    r = rt()
    with _lock:
        res = r.chain.unpause()
        r.guard.audit.append("unfrozen", {"by": "student", **res.as_dict()})
        return res.as_dict()


class GuardIn(BaseModel):
    enabled: bool


@app.post("/guard")
def set_guard(body: GuardIn) -> dict:
    r = rt()
    r.guard.enabled = body.enabled
    r.guard.audit.append("guard_switched", {"enabled": body.enabled})
    return {"enabled": r.guard.enabled}


@app.post("/audit/anchor")
def anchor() -> dict:
    with _lock:
        return rt().guard.anchor_now()


# ---------------------------------------------------------------- attack lab + demo helpers (local only)


class LabEmailIn(BaseModel):
    sender: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


@app.post("/lab/email")
def lab_email(body: LabEmailIn) -> dict:
    """Drop a custom (e.g. phishing) email into the inbox to test the guard live."""
    r = rt()
    text = seed.render(body.body, r.chain.deployment["payees"])
    return {"id": r.ctx.store.add_lab_email(body.sender, body.subject, text)}


@app.post("/lab/jailbreak")
def lab_jailbreak() -> dict:
    """What a fully jailbroken agent could do with its key: skip the guard and push the transfer anyway.
    The gas limit is fixed so the attempt lands on-chain, and the vault reverts it."""
    r = rt()
    with _lock:
        res = r.chain.pay("tHKD", seed.ATTACKER, to_units(5000), "0x" + "00" * 32, force=True)
        r.guard.audit.append("lab_jailbreak", {"to": seed.ATTACKER, "amount": "5000 tHKD", **res.as_dict()})
        return {"attempt": f"agent key sends 5000 tHKD to {seed.ATTACKER}", **res.as_dict()}


@app.post("/lab/rogue-payment")
def lab_rogue_payment() -> dict:
    """A stolen agent key used directly: 500 tHKD to the saved landlord, inside every limit, so the vault
    allows it. Hufu never sees it, so it is not in the audit log, which is what the autopilot's tripwire
    looks for."""
    r = rt()
    with _lock:
        landlord = r.chain.deployment["payees"]["landlord"]
        res = r.chain.pay("tHKD", landlord, to_units(500), "0x" + "00" * 32)
        return {"attempt": "stolen agent key pays the landlord 500 tHKD directly", **res.as_dict()}


class WarpIn(BaseModel):
    days: float = Field(gt=0, le=400)


@app.post("/dev/warp")
def warp(body: WarpIn) -> dict:
    r = rt()
    try:
        r.chain.warp(body.days)
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    return {"chain_time": r.chain.status()["chain_time"]}
