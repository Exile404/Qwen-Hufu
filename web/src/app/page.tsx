"use client";

import { Activity, FlaskConical, Inbox, Scale, ScrollText, ShieldCheck, ShieldOff, Snowflake, Sun } from "lucide-react";
import { useCallback, useState } from "react";

import { AutopilotCard } from "@/components/hufu/autopilot-card";
import { SealLogo } from "@/components/hufu/bits";
import { ChatPanel } from "@/components/hufu/chat-panel";
import { HuitongCard } from "@/components/hufu/huitong-card";
import { ActivityPanel, AttackLab, AuditPanel, CompliancePanel, InboxPanel } from "@/components/hufu/panels";
import { ApprovalsCard, MandatesCard, WalletCard } from "@/components/hufu/side";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  API_URL,
  post,
  type AuditView,
  type AutopilotStatus,
  type ComplianceView,
  type Email,
  type HistoryItem,
  type State,
} from "@/lib/api";
import { usePoll } from "@/lib/use-poll";
import { cn } from "@/lib/utils";

export default function Home() {
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const { data: state, error } = usePoll<State>("/state", version, 3000);
  const { data: history } = usePoll<{ history: HistoryItem[] }>("/history?limit=25", version, 5000);
  const { data: inbox } = usePoll<{ emails: Email[] }>("/inbox", version, 8000);
  const { data: audit } = usePoll<AuditView>("/audit?limit=40", version, 5000);
  const { data: autopilot } = usePoll<AutopilotStatus>("/autopilot", version, 4000);
  const { data: compliance } = usePoll<ComplianceView>("/compliance", version, 8000);
  const [busy, setBusy] = useState(false);

  const explorer = state?.explorer_tx_url ?? null;
  const frozen = state?.status.frozen ?? false;
  const guardOn = state?.guard.enabled ?? true;

  async function act(path: string, body?: unknown) {
    setBusy(true);
    await post(path, body).catch(() => null);
    setBusy(false);
    refresh();
  }

  return (
    <main className="mx-auto w-full max-w-[1400px] flex-1 space-y-4 px-4 py-4 sm:px-6">
      <header className="flex flex-wrap items-center gap-3">
        <SealLogo />
        <div className="mr-auto">
          <h1 className="text-xl font-semibold tracking-tight">Hufu</h1>
          <p className="text-sm text-muted-foreground">A tiger tally for your AI. The agent acts alone within your limits; anything more needs your half.</p>
        </div>
        {state && (
          <>
            <span className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground">
              {state.chain_id === 31337 ? "Local chain" : `Chain ${state.chain_id}`} · test tokens only
            </span>
            <Button
              size="sm"
              variant="outline"
              className={cn(!guardOn && "border-seal text-seal")}
              onClick={() => act("/guard", { enabled: !guardOn })}
              disabled={busy}
            >
              {guardOn ? <ShieldCheck className="text-allow" /> : <ShieldOff />} Guard {guardOn ? "on" : "off"}
            </Button>
            {frozen ? (
              <Button size="sm" variant="outline" onClick={() => act("/unfreeze")} disabled={busy}>
                <Sun /> Unfreeze (you)
              </Button>
            ) : (
              <>
                <Button size="sm" variant="destructive" onClick={() => act("/freeze", { by: "student" })} disabled={busy}>
                  <Snowflake /> Freeze
                </Button>
                <Button size="sm" variant="ghost" onClick={() => act("/freeze", { by: "guardian" })} disabled={busy}>
                  Freeze as guardian
                </Button>
              </>
            )}
          </>
        )}
      </header>

      {error && !state && (
        <Card>
          <CardContent className="space-y-1 py-2 text-sm">
            <p className="font-medium text-seal">Can&apos;t reach the Hufu API at {API_URL}.</p>
            <p className="text-muted-foreground">{error}</p>
            <p className="text-muted-foreground">
              Start it with <code>make api</code> (and <code>make chain</code>, <code>make deploy-local</code>).
            </p>
          </CardContent>
        </Card>
      )}

      {state && (
        <>
          <div className="grid gap-4 lg:grid-cols-12">
            <div className="lg:col-span-7">
              <ChatPanel explorer={explorer} onChange={refresh} />
            </div>
            <div className="space-y-4 lg:col-span-5 lg:h-[calc(100vh-9rem)] lg:overflow-y-auto lg:pr-1">
              <ApprovalsCard state={state} explorer={explorer} onChange={refresh} />
              <AutopilotCard status={autopilot} onChange={refresh} />
              <WalletCard state={state} />
              <MandatesCard state={state} />
              <HuitongCard />
            </div>
          </div>

          <Card>
            <CardContent>
              <Tabs defaultValue="activity">
                <TabsList>
                  <TabsTrigger value="activity"><Activity /> Activity</TabsTrigger>
                  <TabsTrigger value="inbox">
                    <Inbox /> Inbox
                    {inbox && inbox.emails.some((e) => e.security?.risk === "high") && (
                      <span className="size-1.5 rounded-full bg-seal" />
                    )}
                  </TabsTrigger>
                  <TabsTrigger value="audit"><ScrollText /> Audit</TabsTrigger>
                  <TabsTrigger value="compliance"><Scale /> Compliance</TabsTrigger>
                  <TabsTrigger value="lab"><FlaskConical /> Attack lab</TabsTrigger>
                </TabsList>
                <TabsContent value="activity" className="pt-2">
                  <ActivityPanel items={history?.history ?? []} explorer={explorer} />
                </TabsContent>
                <TabsContent value="inbox" className="pt-2">
                  <InboxPanel emails={inbox?.emails ?? []} />
                </TabsContent>
                <TabsContent value="audit" className="pt-2">
                  <AuditPanel audit={audit} explorer={explorer} onChange={refresh} />
                </TabsContent>
                <TabsContent value="compliance" className="pt-2">
                  <CompliancePanel view={compliance} explorer={explorer} />
                </TabsContent>
                <TabsContent value="lab" className="pt-2">
                  <AttackLab guardOn={guardOn} explorer={explorer} onChange={refresh} />
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </>
      )}
    </main>
  );
}
