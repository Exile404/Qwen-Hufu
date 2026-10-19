"use client";

import { Fingerprint, KeyRound, Lock, LockOpen, RefreshCw, ShieldCheck, Trash2, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { TxLink } from "@/components/hufu/bits";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { short, type State, type TxResult } from "@/lib/api";
import {
  createPasskey,
  explainPasskeyError,
  passkeyUnavailable,
  signChallenge,
  studentPost,
  type PasskeyChallenge,
  type Signed,
} from "@/lib/passkey";
import { cn } from "@/lib/utils";

type Act = <T = TxResult>(path: string, body?: Record<string, unknown>) => Promise<T>;

const StudentContext = createContext<Act | null>(null);

/** Runs the student's actions. When the vault has a passkey, shows what is about to be signed and asks the
 *  passkey only after the student confirms (browsers also require that click). */
export function StudentProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{
    ch: PasskeyChallenge;
    resolve: (s: Signed) => void;
    reject: (e: Error) => void;
  } | null>(null);
  const [signing, setSigning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const askToSign = useCallback(
    (ch: PasskeyChallenge) => new Promise<Signed>((resolve, reject) => setPending({ ch, resolve, reject })),
    [],
  );
  const act: Act = useCallback((path, body) => studentPost(path, body, askToSign), [askToSign]);

  async function sign() {
    if (!pending) return;
    setSigning(true);
    setError(null);
    try {
      const signed = await signChallenge(pending.ch);
      pending.resolve(signed);
      setPending(null);
    } catch (e) {
      setError(explainPasskeyError(e));
    } finally {
      setSigning(false);
    }
  }

  const cancel = useCallback(() => {
    pending?.reject(new Error("Cancelled. Nothing was signed."));
    setPending(null);
    setError(null);
  }, [pending]);

  return (
    <StudentContext.Provider value={act}>
      {children}
      {pending && <SignDialog ch={pending.ch} signing={signing} error={error} onSign={sign} onCancel={cancel} />}
    </StudentContext.Provider>
  );
}

export function useStudent(): Act {
  const act = useContext(StudentContext);
  if (!act) throw new Error("useStudent needs a StudentProvider");
  return act;
}

function SignDialog({
  ch,
  signing,
  error,
  onSign,
  onCancel,
}: {
  ch: PasskeyChallenge;
  signing: boolean;
  error: string | null;
  onSign: () => void;
  onCancel: () => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    button.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !signing && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, signing]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="sign-title"
        className="w-full max-w-md space-y-4 rounded-xl border bg-card p-5 text-card-foreground shadow-xl"
      >
        <div className="flex items-start gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-full bg-seal/12 text-seal">
            <Fingerprint className="size-5" />
          </div>
          <div className="min-w-0">
            <h2 id="sign-title" className="font-semibold">
              Sign with your passkey
            </h2>
            <p className="text-sm text-muted-foreground">Your passkey signs exactly this, for this wallet only.</p>
          </div>
        </div>
        <div className="space-y-1 rounded-md border bg-muted/40 p-3">
          <p className="text-sm font-medium break-words">{ch.summary}</p>
          <p className="font-mono text-[11px] break-all text-muted-foreground">
            {ch.call.function}({ch.call.args.map((a) => (typeof a === "string" && a.length > 20 ? short(a, 8, 6) : String(a))).join(", ")})
          </p>
        </div>
        <p className="text-xs text-muted-foreground">
          The vault checks the signature on-chain. It works once and expires in 5 minutes. Your fingerprint, face or PIN
          never leaves your device.
        </p>
        {error && <p className="text-xs font-medium text-seal">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={signing}>
            <X /> Cancel
          </Button>
          <Button ref={button} onClick={onSign} disabled={signing}>
            <Fingerprint /> {signing ? "Waiting for passkey…" : error ? "Try again" : "Sign with passkey"}
          </Button>
        </div>
      </div>
    </div>
  );
}

const ACTION_LABEL: Record<string, string> = {
  approveRequest: "approved a request",
  rejectRequest: "rejected a request",
  setPayee: "added a payee",
  createMandate: "signed a recurring payment",
  unpause: "unfroze the wallet",
  setOwnerKeyRetired: "changed the owner key lock",
  setPasskey: "replaced the passkey",
  removePasskey: "removed the passkey",
};

const noSubscription = () => () => {};

export const passkeyActionLabel = (fn?: string) => (fn ? (ACTION_LABEL[fn] ?? fn) : "");

export function PasskeyCard({ state, explorer, onChange }: { state: State; explorer: string | null; onChange: () => void }) {
  const pk = state.passkey;
  const act = useStudent();
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ what: string; res: TxResult } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const unavailable = useSyncExternalStore(noSubscription, passkeyUnavailable, () => null);

  async function run(what: string, fn: () => Promise<TxResult>) {
    setBusy(what);
    setError(null);
    setResult(null);
    try {
      setResult({ what, res: await fn() });
    } catch (e) {
      setError(explainPasskeyError(e));
    } finally {
      setBusy(null);
      onChange();
    }
  }

  // createPasskey must be the first thing the click does: browsers only allow it right after a user gesture.
  const register = () =>
    run(pk.registered ? "Passkey replaced" : "Passkey registered", async () =>
      act("/passkey", await createPasskey(state.vault, pk.credential_id)),
    );
  const remove = () => run("Passkey removed", () => act("/passkey/remove"));
  const lock = (locked: boolean) =>
    run(locked ? "Owner key locked" : "Owner key unlocked", () => act("/passkey/lock", { locked }));

  return (
    <Card className={cn(pk.owner_key_locked && "ring-1 ring-allow/40")}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Fingerprint className="size-4" /> Your half, on your device
        </CardTitle>
        <CardDescription>
          {!pk.registered
            ? "Right now the owner key on this computer signs your approvals. Move your half to a passkey on your phone, laptop or security key; the vault checks it on-chain."
            : pk.owner_key_locked
              ? "Only your passkey can approve, unfreeze or change limits. A stolen owner key or a hacked agent cannot."
              : "Approvals, rejections and unfreezing now ask your passkey."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {pk.registered && (
          <div className="space-y-1.5 text-sm">
            <p className="flex items-center gap-2">
              <ShieldCheck className="size-4 text-allow" /> Passkey registered on the vault
              <span className="font-mono text-xs text-muted-foreground">{short(pk.credential_id ?? "", 6, 4)}</span>
            </p>
            <p className="flex items-center gap-2">
              {pk.owner_key_locked ? (
                <>
                  <Lock className="size-4 text-allow" /> Owner key locked: it has no power left
                </>
              ) : (
                <>
                  <KeyRound className="size-4 text-ask" /> Owner key still active ({short(state.roles.owner)})
                </>
              )}
            </p>
            <p className="text-xs text-muted-foreground">
              {pk.signatures_used} passkey signature{pk.signatures_used === 1 ? "" : "s"} verified on-chain
            </p>
          </div>
        )}
        {unavailable ? (
          <p className="text-xs text-seal">{unavailable}</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {!pk.registered && (
              <Button size="sm" onClick={register} disabled={busy !== null}>
                <Fingerprint /> Set up passkey
              </Button>
            )}
            {pk.registered && !pk.owner_key_locked && (
              <Button size="sm" onClick={() => lock(true)} disabled={busy !== null}>
                <Lock /> Lock owner key
              </Button>
            )}
            {pk.registered && (
              <Button size="sm" variant="outline" onClick={register} disabled={busy !== null}>
                <RefreshCw /> Replace passkey
              </Button>
            )}
            {pk.owner_key_locked && (
              <Button size="sm" variant="ghost" onClick={() => lock(false)} disabled={busy !== null}>
                <LockOpen /> Unlock owner key
              </Button>
            )}
            {pk.registered && !pk.owner_key_locked && (
              <Button size="sm" variant="ghost" onClick={remove} disabled={busy !== null}>
                <Trash2 /> Remove
              </Button>
            )}
          </div>
        )}
        {busy && <p className="text-xs text-muted-foreground">Waiting for your passkey…</p>}
        {result && (
          <p className="text-xs text-muted-foreground">
            {result.res.status === "success" ? result.what : `Not done: ${result.res.hint ?? result.res.error}`}{" "}
            <TxLink hash={result.res.tx_hash} explorer={explorer} />
          </p>
        )}
        {error && <p className="text-xs text-seal">{error}</p>}
        {!pk.registered && !unavailable && (
          <p className="text-xs text-muted-foreground">
            No authenticator on this computer? Choose “use a phone” to scan a QR code, or turn on Chrome DevTools’
            virtual authenticator (More tools › WebAuthn).
          </p>
        )}
      </CardContent>
    </Card>
  );
}
