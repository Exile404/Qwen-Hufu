import { Ban, CircleCheck, Hourglass } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { short, type Decision } from "@/lib/api";

/** A vermilion seal (印章) with 虎符: the brand mark. */
export function SealLogo({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "grid size-11 shrink-0 place-items-center rounded-md bg-seal text-seal-foreground shadow-sm",
        "font-serif text-[15px] leading-[1.05] font-bold tracking-tight [writing-mode:vertical-rl]",
        className,
      )}
    >
      虎符
    </div>
  );
}

const DECISION: Record<Decision, { label: string; className: string; icon: ReactNode }> = {
  ALLOW: { label: "Allow", className: "bg-allow/12 text-allow border-allow/30", icon: <CircleCheck /> },
  ASK: { label: "Ask Ayaan", className: "bg-ask/15 text-ask border-ask/40", icon: <Hourglass /> },
  BLOCK: { label: "Block", className: "bg-seal/12 text-seal border-seal/40", icon: <Ban /> },
};

export function DecisionBadge({ decision }: { decision: Decision }) {
  const d = DECISION[decision];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide [&_svg]:size-3",
        d.className,
      )}
    >
      {d.icon}
      {d.label}
    </span>
  );
}

const RISK = {
  high: "bg-seal/12 text-seal border-seal/40",
  medium: "bg-ask/15 text-ask border-ask/40",
  low: "bg-muted text-muted-foreground border-border",
};

export function RiskBadge({ risk }: { risk: "high" | "medium" | "low" }) {
  return (
    <span className={cn("rounded-md border px-1.5 py-0.5 text-[11px] font-semibold uppercase", RISK[risk])}>
      {risk} risk
    </span>
  );
}

export function TxLink({ hash, explorer }: { hash?: string | null; explorer?: string | null }) {
  if (!hash) return null;
  const label = <span className="font-mono text-xs">{short(hash, 8, 6)}</span>;
  return explorer ? (
    <a href={`${explorer}${hash}`} target="_blank" rel="noreferrer" className="underline underline-offset-2">
      {label}
    </a>
  ) : (
    <span title={hash} className="cursor-help text-muted-foreground">
      {label}
    </span>
  );
}

/** Minimal formatting for model replies: **bold**, `code`, line breaks. */
export function RichText({ text }: { text: string }) {
  return (
    <div className="space-y-1.5 text-sm leading-relaxed">
      {text.split("\n").map((line, i) =>
        line.trim() === "" ? null : (
          <p key={i} className="break-words">
            {line.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, j) =>
              part.startsWith("**") ? (
                <strong key={j}>{part.slice(2, -2)}</strong>
              ) : part.startsWith("`") ? (
                <code key={j} className="rounded bg-muted px-1 font-mono text-[0.8em] break-all">
                  {part.slice(1, -1)}
                </code>
              ) : (
                part.replace(/^#+\s*/, "")
              ),
            )}
          </p>
        ),
      )}
    </div>
  );
}
