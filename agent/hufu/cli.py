"""Terminal chat with the agent: `make cli`.

Plain text goes to the agent. Slash commands act as the student (owner) or show state.
"""

import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hufu import approvals, compliance, planner, student
from hufu.agent import Agent, Step
from hufu.autopilot import Autopilot
from hufu.chain import Chain, ChainNotDeployed
from hufu.config import get_settings
from hufu.guard import Guard
from hufu.store import Store
from hufu.tools import Context

console = Console()
TIER_STYLE = {"T0": "dim", "T1": "cyan", "T2": "yellow", "?": "red"}
DECISION_STYLE = {"ALLOW": "green", "ASK": "yellow", "BLOCK": "bold red"}

PASSKEY_ONLY = (
    "[yellow]This wallet has a passkey: your half is signed on your device. Approve it in the dashboard "
    "(make web), where your passkey is asked to sign.[/]"
)

HELP = """[bold]Talk to the agent in plain English.[/] Try:
  how much can I still spend today?    pay my rent    set up my tuition instalments
  pay my tuition    split utilities with Kevin    check my inbox and handle anything urgent

[bold]Student (owner) commands[/]
  /approvals        list requests waiting for you
  /approve [N]      approve request N, or the latest one (owner key; with a passkey, use the dashboard)
  /reject [N]       reject request N, or the latest one
  /unfreeze         unfreeze the wallet
  /passkey          passkey status (register and use it in the dashboard)
[bold]Guardian[/]
  /freeze           freeze the wallet with the guardian key (no approval needed to stop)
[bold]Guard & audit[/]
  /guard on|off     switch the off-chain guard (off = the "before" baseline; the vault still enforces limits)
  /audit            recent audit entries and a full hash-chain check
  /anchor           anchor the audit head on-chain now
[bold]Autopilot[/]
  /autopilot        run the autopilot once now: tripwire, due payments, funding, inbox scan
  /plan [DAYS]      cash-flow plan for the next DAYS (default 30)
  /compliance       screening list status and records of transfers of HK$8,000 or more
[bold]Info[/]
  /status  /mandates  /history  /payees
[bold]Session[/]
  /warp DAYS        move local-chain time forward (demo recurring payments)
  /reset            clear the conversation      /help      /quit"""


def show_step(s: Step) -> None:
    style = TIER_STYLE.get(s.tier, "")
    args = ", ".join(f"{k}={v}" for k, v in s.args.items())
    r = s.result
    status = r.get("result") or r.get("status") or "ok"
    mark = {"success": "✓", "done": "✓", "waiting_for_student_approval": "⏳"}.get(status, "")
    if status in ("rejected", "reverted", "error"):
        mark = "✗"
    if status == "blocked_by_guard":
        mark = "⛔"
    g = r.get("guard", {})
    decision = g.get("decision")
    tag = f"[{DECISION_STYLE[decision]}]{decision}[/] " if decision else ""
    line = f"  {tag}[{style}]{s.tier} {s.tool}({args})[/] {mark} {status}"
    if r.get("tx_hash"):
        line += f"  tx {r['tx_hash'][:12]}…"
    if r.get("error"):
        line += f"  [red]{r['error']}[/]"
    console.print(line + f"  [dim]{s.ms} ms[/]")
    for reason in g.get("reasons", []) if decision in ("ASK", "BLOCK") else []:
        console.print(f"      [{DECISION_STYLE[decision]}]↳ {reason}[/]")
    for w in g.get("warnings", []):
        console.print(f"      [yellow]⚠ {w}[/]")
    for e in r.get("emails", []):
        sec = e.get("security", {})
        if sec.get("risk") in ("high", "medium"):
            console.print(
                f"      [red]✉ #{e['id']} {e['subject'][:50]}: risk {sec['risk']} ({'; '.join(sec['findings'])})[/]"
            )


def show_json(title: str, data: object) -> None:
    console.print(
        Panel(json.dumps(data, indent=2, default=str, ensure_ascii=False), title=title, expand=False)
    )


def show_approvals(ctx: Context) -> None:
    pending = ctx.store.approvals("pending")
    if not pending:
        console.print("[dim]No requests waiting.[/]")
        return
    t = Table(title="Waiting for your approval")
    for col in ("#", "kind", "details", "reason"):
        t.add_column(col)
    for a in pending:
        t.add_row(str(a["id"]), a["kind"], json.dumps(a["payload"], ensure_ascii=False), a["reason"])
    console.print(t)


def command(ctx: Context, agent: Agent, guard: Guard, line: str) -> bool:
    """Handle a slash command. Returns False to quit."""
    cmd, *rest = line[1:].split()
    arg = rest[0] if rest else None
    if cmd in ("quit", "exit", "q"):
        return False
    if cmd == "help":
        console.print(HELP)
    elif cmd == "status":
        show_json("Wallet", ctx.chain.status())
    elif cmd == "mandates":
        show_json("Recurring payments", ctx.chain.mandates())
    elif cmd == "history":
        show_json("History", ctx.chain.history(15, ctx.store.memo))
    elif cmd == "payees":
        show_json("Payees", ctx.chain.payees())
    elif cmd == "approvals":
        show_approvals(ctx)
    elif cmd in ("approve", "reject"):
        pending = [a["id"] for a in ctx.store.approvals("pending")]
        target = int(arg) if arg else (pending[-1] if pending else None)
        if target is None or target not in pending:
            console.print(
                f"[yellow]No pending request #{arg}. Pending: {', '.join(f'#{i}' for i in pending) or 'none'}[/]"
            )
        else:
            fn = approvals.approve if cmd == "approve" else approvals.reject
            try:
                show_json(f"{cmd} #{target}", fn(ctx, target))
            except student.PasskeyRequired:
                console.print(PASSKEY_ONLY)
    elif cmd == "unfreeze":
        try:
            show_json("unfreeze", student.unfreeze(ctx).as_dict())
        except student.PasskeyRequired:
            console.print(PASSKEY_ONLY)
    elif cmd == "passkey":
        show_json("Passkey", {k: v for k, v in ctx.chain.passkey().items() if k not in ("x", "y")})
    elif cmd == "freeze":
        show_json("freeze (guardian)", ctx.chain.pause(ctx.chain.guardian).as_dict())
    elif cmd == "guard" and arg in ("on", "off"):
        guard.enabled = arg == "on"
        console.print(f"Guard is now [bold]{arg}[/].")
    elif cmd == "audit":
        for e in reversed(guard.audit.recent(12)):
            console.print(
                f"[dim]#{e['id']} {e['ts'][11:19]} {e['hash'][:10]}…[/] {e['kind']}: "
                f"{json.dumps(e['data'], ensure_ascii=False)[:110]}"
            )
        show_json("hash-chain check", guard.audit.verify())
    elif cmd == "anchor":
        show_json("anchored", guard.anchor_now())
    elif cmd == "warp" and arg:
        ctx.chain.warp(float(arg))
        console.print(f"Chain time is now {ctx.chain.status()['chain_time']}")
    elif cmd == "autopilot":
        run = Autopilot(ctx, guard).tick()
        for e in run["events"] or [{"level": "info", "title": "Nothing to do."}]:
            colour = {"critical": "red", "warning": "yellow", "action": "green"}.get(e["level"], "dim")
            console.print(f"[{colour}]{e['level']:>8}[/] {e['title']}")
    elif cmd == "plan":
        p = planner.plan(ctx.chain, int(arg) if arg else 30)
        console.print(p["summary"])
        show_json("Upcoming", p["upcoming"])
    elif cmd == "compliance":
        show_json("Screening", compliance.screening_status())
        show_json("Transfer records (HK$8,000 or more)", compliance.records(ctx, 10))
    elif cmd == "reset":
        agent.reset()
        console.print("[dim]Conversation cleared.[/]")
    else:
        console.print("Unknown command. /help")
    return True


def main() -> int:
    s = get_settings()
    try:
        chain = Chain(s)
    except (FileNotFoundError, ChainNotDeployed) as e:
        console.print(f"[red]{e}[/]")
        return 1
    ctx = Context(chain, Store(s.db_path, chain.vault.address))
    ctx.store.bind_deployment(chain.deployment_marker())
    guard = Guard(ctx)
    agent = Agent(ctx, guard=guard)
    console.print(
        Panel.fit(
            f"[bold]Hufu 虎符[/] agent CLI · model {s.llm_model} · vault {chain.vault.address}\n"
            "Guard [green]on[/]. /help for commands."
        )
    )
    while True:
        try:
            line = console.input("[bold green]you ›[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        try:
            if line.startswith("/"):
                if not command(ctx, agent, guard, line):
                    break
                continue
            with console.status("thinking…"):
                turn = agent.chat(line)
            for step in turn.steps:
                show_step(step)
            console.print(Panel(turn.reply or "(no reply)", title="Hufu", border_style="magenta"))
            if turn.anchor:
                console.print(
                    f"[dim]audit head {turn.anchor['head'][:12]}… anchored on-chain, "
                    f"tx {str(turn.anchor.get('tx_hash'))[:12]}…[/]"
                )
        except Exception as e:  # noqa: BLE001 - keep the REPL alive and show the error
            console.print(f"[red]{type(e).__name__}: {e}[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
