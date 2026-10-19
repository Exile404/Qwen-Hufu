"use client";

import { Bot, CalendarClock, CircleCheck, Play, Power, Siren, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fmt, post, type Alert, type AutopilotLevel, type AutopilotStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const LEVEL: Record<AutopilotLevel, { icon: React.ReactNode; className: string; rank: number }> = {
  critical: { icon: <Siren className="size-4" />, className: "border-seal/50 bg-seal/8 text-seal", rank: 0 },
  warning: { icon: <TriangleAlert className="size-4" />, className: "border-ask/40 bg-ask/10", rank: 1 },
  action: { icon: <CircleCheck className="size-4 text-allow" />, className: "border-allow/30 bg-allow/5", rank: 2 },
  info: { icon: <CircleCheck className="size-4" />, className: "", rank: 3 },
};

function time(iso: string) {
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function AlertRow({ a }: { a: Alert }) {
  const l = LEVEL[a.level] ?? LEVEL.info;
  return (
    <li className={cn("flex gap-2 rounded-md border px-2.5 py-2 text-sm", l.className)}>
      <span className="mt-0.5 shrink-0">{l.icon}</span>
      <span className="min-w-0 flex-1">
        {a.title}
        <span className="ml-1.5 text-xs text-muted-foreground">{time(a.created_at)}</span>
      </span>
    </li>
  );
}

export function AutopilotCard({ status, onChange }: { status: AutopilotStatus | null; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!status) return null;

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await post(path, body);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      onChange();
    }
  }

  const plan = status.plan;
  const alerts = [...status.alerts]
    .sort((a, b) => (LEVEL[a.level]?.rank ?? 3) - (LEVEL[b.level]?.rank ?? 3) || b.id - a.id)
    .slice(0, 4);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4" /> Autopilot
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-xs font-normal",
              status.enabled ? "bg-allow/12 text-allow" : "bg-muted text-muted-foreground",
            )}
          >
            {status.enabled ? `on · every ${status.interval_s}s` : "off"}
          </span>
        </CardTitle>
        <CardDescription>
          Acts between chats: pays signed bills when due, converts ahead so they are covered, flags scam emails, and
          freezes the wallet if the agent key moves money Hufu did not send.
        </CardDescription>
        <CardAction className="flex gap-1.5">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => act("/autopilot/run")}>
            <Play /> Run now
          </Button>
          <Button
            size="sm"
            variant={status.enabled ? "outline" : "default"}
            disabled={busy}
            onClick={() => act("/autopilot", { enabled: !status.enabled })}
          >
            <Power /> {status.enabled ? "Turn off" : "Turn on"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <CalendarClock className="size-3.5" /> Next {plan.days} days
          </p>
          {plan.upcoming.length === 0 ? (
            <p className="text-sm text-muted-foreground">No recurring payments due.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {plan.upcoming.slice(0, 4).map((i) => (
                <li key={`${i.label}-${i.due}`} className="flex justify-between gap-2">
                  <span className="truncate">
                    <span className="tabular-nums text-muted-foreground">{i.due.slice(5)}</span> {i.label}
                  </span>
                  <span className="shrink-0 tabular-nums">
                    {fmt(i.amount)} {i.token}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className={cn("text-xs", plan.covered && !plan.hkd_shortfall ? "text-allow" : "text-ask")}>
            {plan.hkd_shortfall
              ? `tHKD is ${fmt(plan.hkd_shortfall)} short: ${fmt(plan.convert_usd)} tUSD to convert${
                  plan.days_to_convert > 1 ? ` over ${plan.days_to_convert} days` : ""
                }.`
              : "Covered by the current tHKD balance."}
          </p>
        </div>

        {alerts.length > 0 && <ul className="space-y-1.5">{alerts.map((a) => <AlertRow key={a.id} a={a} />)}</ul>}

        <p className="text-xs text-muted-foreground">
          {status.last_run
            ? `Last run ${time(status.last_run.at)}: ${
                status.last_run.events.length ? `${status.last_run.events.length} event(s)` : "nothing to do"
              }.`
            : "Not run yet."}
        </p>
        {error && <p className="text-sm text-seal">{error}</p>}
      </CardContent>
    </Card>
  );
}
