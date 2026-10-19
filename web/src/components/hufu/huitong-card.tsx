"use client";

import { ArrowLeftRight, Ban } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, fmt } from "@/lib/api";
import { cn } from "@/lib/utils";

type Route = { route: string; recipient_gets: number; total_cost: number; cost_percent: number; speed: string; note: string };
type Quote = {
  corridor: string;
  sender_pays: string;
  mid_market_value: string;
  routes: Route[];
  not_offered: { route: string; reason: string }[];
  source: string;
};

const DIRECTIONS = {
  family_to_me: { label: "Family → me", currency: "USD", amount: 500, receive: "HKD" },
  me_to_family: { label: "Me → family", currency: "HKD", amount: 2000, receive: "BDT" },
} as const;

type Direction = keyof typeof DIRECTIONS;

export function HuitongCard() {
  const [direction, setDirection] = useState<Direction>("family_to_me");
  const [amount, setAmount] = useState<string>("500");
  const [quote, setQuote] = useState<Quote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cfg = DIRECTIONS[direction];

  useEffect(() => {
    const n = Number(amount);
    if (!n || n <= 0) return;
    const id = setTimeout(() => {
      api<Quote>(`/huitong?direction=${direction}&amount=${n}&currency=${cfg.currency}`)
        .then((q) => {
          setQuote(q);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    }, 250);
    return () => clearTimeout(id);
  }, [direction, amount, cfg.currency]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ArrowLeftRight className="size-4" /> Huitong 汇通 · Dhaka ⇄ Hong Kong
        </CardTitle>
        <CardDescription>Licensed routes only, ranked by what actually arrives. Sample quotes.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {(Object.keys(DIRECTIONS) as Direction[]).map((d) => (
            <Button
              key={d}
              size="xs"
              variant={direction === d ? "default" : "outline"}
              onClick={() => {
                setDirection(d);
                setAmount(String(DIRECTIONS[d].amount));
              }}
            >
              {DIRECTIONS[d].label}
            </Button>
          ))}
          <div className="ml-auto flex items-center gap-1.5">
            <Input value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" className="h-7 w-24 text-right" aria-label="Amount" />
            <span className="text-sm text-muted-foreground">{cfg.currency}</span>
          </div>
        </div>
        {error && <p className="text-xs text-seal">{error}</p>}
        {quote && (
          <>
            <p className="text-xs text-muted-foreground">
              {quote.corridor}: sender pays {quote.sender_pays}; worth {quote.mid_market_value} at the mid-market rate.
            </p>
            <div className="space-y-1.5">
              {quote.routes.map((r, i) => (
                <div key={r.route} className={cn("rounded-md border p-2", i === 0 && "border-allow/50 bg-allow/5")}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="text-sm font-medium">
                      {i === 0 && <span className="mr-1 text-xs text-allow">Best</span>}
                      {r.route}
                    </span>
                    <span className="shrink-0 tabular-nums text-sm font-semibold">
                      {fmt(r.recipient_gets)} {cfg.receive}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    cost {fmt(r.total_cost)} {cfg.receive} ({r.cost_percent}%) · {r.speed}
                    {r.note ? ` · ${r.note}` : ""}
                  </p>
                </div>
              ))}
              {quote.not_offered.map((x) => (
                <div key={x.route} className="flex gap-2 rounded-md border border-dashed p-2 text-xs text-muted-foreground">
                  <Ban className="mt-0.5 size-3.5 shrink-0 text-seal" />
                  <span>
                    <strong className="text-foreground">{x.route}: not offered.</strong> {x.reason}
                  </span>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground">{quote.source}</p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
