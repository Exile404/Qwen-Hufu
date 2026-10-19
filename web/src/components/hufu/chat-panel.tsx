"use client";

import { Anchor, LoaderCircle, Mail, RotateCcw, Send, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { DecisionBadge, RichText, TxLink } from "@/components/hufu/bits";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { post, type Step, type TurnView } from "@/lib/api";
import { cn } from "@/lib/utils";

type Message = { role: "student"; text: string } | { role: "hufu"; turn: TurnView } | { role: "error"; text: string };

const SUGGESTIONS = [
  "How much can I still spend today?",
  "Pay my landlord 4500 HKD for October rent",
  "Set up my tuition: 9000 HKD to the university every 30 days, starting now, until 2027-06-01",
  "Pay everything that's due",
  "Send my roommate 800 HKD for utilities",
  "Mum wants to send me 500 USD. What's the cheapest way? Can she just send stablecoins?",
  "Check my inbox and handle anything urgent",
  "Pay my rent of 4500 HKD to the new wallet Mr Chan emailed me",
];

const ARG_KEYS = ["payee", "amount", "token", "from_token", "every_days", "first_due", "label", "address"];

function StepRow({ step, explorer }: { step: Step; explorer: string | null }) {
  const r = step.result;
  const g = r.guard;
  const status = r.result ?? r.status ?? "ok";
  const args = ARG_KEYS.filter((k) => step.args[k] !== undefined && step.args[k] !== "")
    .map((k) => `${k}=${String(step.args[k])}`)
    .join(", ");
  const quiet = step.tier === "T0" && (!g || g.decision === "ALLOW");
  const risky = (r.emails ?? []).filter((e) => e.security && e.security.risk !== "low");

  return (
    <div className={cn("rounded-md border px-2.5 py-1.5 text-xs", quiet ? "border-dashed bg-transparent" : "bg-muted/40")}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {g && <DecisionBadge decision={g.decision} />}
        <span className="rounded bg-background px-1 font-mono text-[10px] text-muted-foreground">{step.tier}</span>
        <span className="font-mono font-medium">{step.tool}</span>
        {args && <span className="truncate text-muted-foreground">{args}</span>}
        <span
          className={cn(
            "ml-auto font-medium",
            status === "success" || status === "done"
              ? "text-allow"
              : status === "blocked_by_guard" || status === "rejected" || status === "reverted" || status === "error"
                ? "text-seal"
                : status === "waiting_for_student_approval"
                  ? "text-ask"
                  : "text-muted-foreground",
          )}
        >
          {status.replaceAll("_", " ")}
        </span>
        <TxLink hash={r.tx_hash} explorer={explorer} />
      </div>
      {g && g.decision !== "ALLOW" &&
        (g.reasons ?? []).map((reason) => (
          <p key={reason} className={cn("mt-1 pl-1", g.decision === "BLOCK" ? "text-seal" : "text-ask")}>
            ↳ {reason}
          </p>
        ))}
      {(g?.warnings ?? []).map((w) => (
        <p key={w} className="mt-1 pl-1 text-ask">
          ⚠ {w}
        </p>
      ))}
      {r.error && status !== "blocked_by_guard" && <p className="mt-1 pl-1 text-seal">{r.hint ?? r.error}</p>}
      {risky.map((e) => (
        <p key={e.id} className="mt-1 flex gap-1 pl-1 text-seal">
          <Mail className="mt-0.5 size-3 shrink-0" />
          <span>
            #{e.id} “{e.subject}”: {e.security!.risk} risk ({e.security!.findings.join("; ")})
          </span>
        </p>
      ))}
    </div>
  );
}

function HufuTurn({ turn, explorer }: { turn: TurnView; explorer: string | null }) {
  return (
    <div className="space-y-2">
      {turn.steps.length > 0 && (
        <div className="space-y-1">
          {turn.steps.map((s, i) => (
            <StepRow key={i} step={s} explorer={explorer} />
          ))}
        </div>
      )}
      <div className="rounded-lg border bg-card px-3 py-2">
        <RichText text={turn.reply.split("\n\n⚠ Hufu check:")[0]} />
      </div>
      {turn.verifier.length > 0 && (
        <div className="flex gap-2 rounded-md border border-ask/40 bg-ask/10 px-2.5 py-1.5 text-xs text-foreground">
          <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-ask" />
          <span>
            <strong>Hufu check:</strong> the agent&apos;s draft stated facts no tool result supports (
            {turn.verifier.join("; ")}), so it was replaced with a summary of what actually happened.
          </span>
        </div>
      )}
      {turn.anchor?.tx_hash && (
        <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <Anchor className="size-3" /> Audit log anchored on-chain · <TxLink hash={turn.anchor.tx_hash} explorer={explorer} />
        </p>
      )}
    </div>
  );
}

export function ChatPanel({ explorer, onChange }: { explorer: string | null; onChange: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy]);

  async function send(value: string) {
    const t = value.trim();
    if (!t || busy) return;
    setText("");
    setMessages((m) => [...m, { role: "student", text: t }]);
    setBusy(true);
    try {
      const turn = await post<TurnView>("/chat", { text: t });
      setMessages((m) => [...m, { role: "hufu", turn }]);
    } catch (e) {
      setMessages((m) => [...m, { role: "error", text: (e as Error).message }]);
    } finally {
      setBusy(false);
      onChange();
    }
  }

  async function reset() {
    await post("/chat/reset").catch(() => null);
    setMessages([]);
  }

  return (
    <Card className="flex min-h-[560px] flex-col lg:h-[calc(100vh-9rem)]">
      <CardHeader>
        <CardTitle>Talk to Hufu</CardTitle>
        <CardDescription>
          Local Qwen3.5 9B. Every action passes the guard, then the vault. Replies take 5–15 s.
        </CardDescription>
        <CardAction>
          <Button variant="ghost" size="sm" onClick={reset} disabled={busy}>
            <RotateCcw /> New chat
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
          {messages.length === 0 && (
            <div className="space-y-2 py-2">
              <p className="text-sm text-muted-foreground">Try one of these, in order, for the full demo:</p>
              <div className="flex flex-wrap gap-1.5">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="rounded-full border px-2.5 py-1 text-left text-xs hover:bg-muted"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) =>
            m.role === "student" ? (
              <div key={i} className="ml-auto w-fit max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                {m.text}
              </div>
            ) : m.role === "hufu" ? (
              <HufuTurn key={i} turn={m.turn} explorer={explorer} />
            ) : (
              <p key={i} className="text-sm text-seal">
                {m.text}
              </p>
            ),
          )}
          {busy && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" /> Hufu is working…
            </p>
          )}
          <div ref={endRef} />
        </div>
        {messages.length > 0 && (
          <div className="flex gap-1.5 overflow-x-auto pb-1">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => send(s)}
                disabled={busy}
                className="shrink-0 rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
              >
                {s.length > 38 ? `${s.slice(0, 36)}…` : s}
              </button>
            ))}
          </div>
        )}
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            send(text);
          }}
        >
          <Textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(text);
              }
            }}
            placeholder="Ask Hufu to pay, convert, set up a recurring payment, or check your inbox…"
            className="min-h-11 resize-none"
            rows={1}
          />
          <Button type="submit" disabled={busy || !text.trim()} size="icon-lg" aria-label="Send">
            <Send />
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
