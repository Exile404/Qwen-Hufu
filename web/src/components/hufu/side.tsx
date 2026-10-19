"use client";

import { Check, Clock, Fingerprint, KeyRound, Repeat, Snowflake, Wallet, X } from "lucide-react";
import { useState } from "react";

import { TxLink } from "@/components/hufu/bits";
import { useStudent } from "@/components/hufu/passkey";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fmt, short, type Approval, type State, type TxResult } from "@/lib/api";
import { cn } from "@/lib/utils";

export function WalletCard({ state }: { state: State }) {
  const st = state.status;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Wallet className="size-4" /> Ayaan&apos;s vault
        </CardTitle>
        <CardDescription className="font-mono text-xs">{short(state.vault, 10, 8)}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {st.frozen && (
          <div className="flex items-center gap-2 rounded-md bg-seal px-3 py-2 text-sm font-medium text-seal-foreground">
            <Snowflake className="size-4" /> Frozen. The agent cannot move money until you unfreeze.
          </div>
        )}
        {Object.entries(st.tokens).map(([sym, t]) => {
          const used = t.daily_limit ? 1 - t.left_today / t.daily_limit : 0;
          return (
            <div key={sym} className="space-y-1.5">
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-muted-foreground">{sym}</span>
                <span className="text-2xl font-semibold tabular-nums">{fmt(t.balance)}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-bronze transition-all" style={{ width: `${Math.min(100, used * 100)}%` }} />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>
                  {fmt(t.left_today)} of {fmt(t.daily_limit)} left today
                </span>
                <span>max {fmt(t.per_payment_limit)} per payment</span>
              </div>
            </div>
          );
        })}
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <KeyRound className="size-3" /> Agent key {st.agent_active ? "active" : "inactive"} · expires {st.agent_session_expires}
        </p>
      </CardContent>
    </Card>
  );
}

function describe(a: Approval) {
  const p = a.payload;
  if (a.kind === "payment") return `Pay ${p.amount} ${p.token} to ${p.payee ?? short(String(p.to))}`;
  if (a.kind === "mandate")
    return `${p.amount} ${p.token} to ${p.payee} every ${p.every_days} days${p.end_date ? ` until ${p.end_date}` : ""}`;
  return `Add payee “${p.label}” ${short(String(p.address))}`;
}

export function ApprovalsCard({
  state,
  explorer,
  onChange,
}: {
  state: State;
  explorer: string | null;
  onChange: () => void;
}) {
  const [busy, setBusy] = useState<number | null>(null);
  const [last, setLast] = useState<{ id: number; verb: string; res: TxResult } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const act = useStudent();
  const withPasskey = state.passkey.registered;

  async function decide(id: number, verb: "approve" | "reject") {
    setBusy(id);
    setError(null);
    try {
      const res = await act(`/approvals/${id}/${verb}`);
      setLast({ id, verb, res });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      onChange();
    }
  }

  return (
    <Card className={cn(state.approvals.length > 0 && "ring-2 ring-ask/50")}>
      <CardHeader>
        <CardTitle>Your half of the tally</CardTitle>
        <CardDescription>
          Anything beyond the agent&apos;s limits waits here for your signature
          {withPasskey ? ", made with your passkey" : ""}.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {state.approvals.length === 0 && <p className="text-sm text-muted-foreground">Nothing waiting for you.</p>}
        {state.approvals.map((a) => {
          const [reason, ...warnings] = a.reason.split(" ⚠ ").map((s) => s.replace(/^⚠ /, ""));
          const risky = warnings.length > 0 || a.reason.startsWith("⚠");
          return (
            <div key={a.id} className={cn("space-y-2 rounded-md border p-3", risky && "border-seal/50 bg-seal/5")}>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-medium">{describe(a)}</p>
                  <p className="text-xs text-muted-foreground">
                    #{a.id} · {a.kind}
                    {a.payload.memo ? ` · “${a.payload.memo}”` : ""}
                  </p>
                </div>
              </div>
              {reason && <p className="text-xs text-muted-foreground">Why: {reason}</p>}
              {(a.reason.startsWith("⚠") ? [reason, ...warnings] : warnings).map((w) => (
                <p key={w} className="text-xs font-medium text-seal">
                  ⚠ {w}
                </p>
              ))}
              <div className="flex gap-2">
                <Button size="sm" onClick={() => decide(a.id, "approve")} disabled={busy !== null} variant={risky ? "outline" : "default"}>
                  {withPasskey ? <Fingerprint /> : <Check />} {withPasskey ? "Approve with passkey" : "Approve"}
                </Button>
                <Button size="sm" variant={risky ? "default" : "outline"} onClick={() => decide(a.id, "reject")} disabled={busy !== null}>
                  <X /> Reject
                </Button>
              </div>
            </div>
          );
        })}
        {last && (
          <p className="text-xs text-muted-foreground">
            #{last.id} {last.verb === "approve" ? "approved" : "rejected"} · {last.res.status}{" "}
            <TxLink hash={last.res.tx_hash} explorer={explorer} />
            {last.res.error && <span className="text-seal"> · {last.res.hint ?? last.res.error}</span>}
          </p>
        )}
        {error && <p className="text-xs text-seal">{error}</p>}
      </CardContent>
    </Card>
  );
}

export function MandatesCard({ state }: { state: State }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Repeat className="size-4" /> Recurring payments
        </CardTitle>
        <CardDescription>Signed by you. The agent can only pay them when due, for exactly this amount.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {state.mandates.map((m) => (
          <div key={m.id} className={cn("flex items-center justify-between gap-2 text-sm", !m.active && "opacity-50")}>
            <div className="min-w-0">
              <p className="truncate font-medium">{m.label}</p>
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Clock className="size-3" /> {m.due_now ? "due now" : `next ${m.next_due.slice(0, 10)}`} · every {m.every_days} days
              </p>
            </div>
            <span className="shrink-0 tabular-nums">
              {fmt(m.amount)} {m.token}
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
