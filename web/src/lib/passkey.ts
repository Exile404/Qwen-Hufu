// The student's half as a passkey (WebAuthn). The browser talks to the passkey on the student's phone, laptop
// or security key; the vault checks the signature on-chain. Nothing here holds a key.

import { API_URL, type TxResult } from "@/lib/api";

export type PasskeyChallenge = {
  challenge_id: string;
  challenge: string; // base64url of the 32 bytes the vault expects to be signed
  credential_id: string | null;
  valid_until: number;
  summary: string;
  call: { function: string; args: unknown[] };
};

export type Signed = {
  challenge_id: string;
  assertion: { credential_id: string; authenticator_data: string; client_data_json: string; signature: string };
};

export const b64url = (buf: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(buf instanceof Uint8Array ? buf : new Uint8Array(buf))))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

export function fromB64url(s: string): Uint8Array<ArrayBuffer> {
  const bin = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4));
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Why passkeys cannot work on this page, or null if they can. */
export function passkeyUnavailable(): string | null {
  if (typeof window === "undefined") return null;
  if (!window.PublicKeyCredential || !navigator.credentials) return "This browser does not support passkeys.";
  if (/^\d+\.\d+\.\d+\.\d+$|^\[/.test(location.hostname))
    return `Passkeys need a domain name: open the dashboard at http://localhost:${location.port || "3000"} instead of ${location.hostname}.`;
  if (!window.isSecureContext) return "Passkeys need a secure page (https, or http://localhost).";
  return null;
}

export function explainPasskeyError(e: unknown): string {
  const name = (e as { name?: string })?.name;
  if (name === "NotAllowedError") return "The passkey request was cancelled or timed out.";
  if (name === "InvalidStateError") return "This device already holds the vault's passkey.";
  if (name === "SecurityError") return passkeyUnavailable() ?? "The browser blocked the passkey request on this page.";
  return (e as Error)?.message ?? String(e);
}

/** Create a new passkey (P-256 / ES256, user verification required). Call straight from a click. */
export async function createPasskey(vault: string, exclude: string | null) {
  const cred = (await navigator.credentials.create({
    publicKey: {
      rp: { name: "Hufu" },
      user: {
        id: crypto.getRandomValues(new Uint8Array(16)),
        name: `hufu-${vault.slice(2, 8).toLowerCase()}`,
        displayName: `Hufu vault ${vault.slice(0, 6)}…${vault.slice(-4)}`,
      },
      challenge: crypto.getRandomValues(new Uint8Array(32)), // registration is not attested; the vault only needs the key
      pubKeyCredParams: [{ type: "public-key", alg: -7 }], // ES256: the curve the vault verifies
      authenticatorSelection: { residentKey: "preferred", userVerification: "required" },
      excludeCredentials: exclude ? [{ type: "public-key", id: fromB64url(exclude) }] : [],
      attestation: "none",
      timeout: 120_000,
    },
  })) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was created.");
  const res = cred.response as AuthenticatorAttestationResponse;
  const spki = res.getPublicKey?.();
  if (!spki || res.getPublicKeyAlgorithm?.() !== -7) throw new Error("This authenticator did not create a P-256 passkey.");
  return { credential_id: b64url(cred.rawId), public_key: b64url(spki) };
}

/** Have the registered passkey sign the vault's challenge. Call straight from a click. */
export async function signChallenge(ch: PasskeyChallenge): Promise<Signed> {
  const cred = (await navigator.credentials.get({
    publicKey: {
      challenge: fromB64url(ch.challenge),
      allowCredentials: ch.credential_id ? [{ type: "public-key", id: fromB64url(ch.credential_id) }] : [],
      userVerification: "required",
      timeout: 120_000,
    },
  })) as PublicKeyCredential | null;
  if (!cred) throw new Error("The passkey did not sign.");
  const r = cred.response as AuthenticatorAssertionResponse;
  return {
    challenge_id: ch.challenge_id,
    assertion: {
      credential_id: b64url(cred.rawId),
      authenticator_data: b64url(r.authenticatorData),
      client_data_json: b64url(r.clientDataJSON),
      signature: b64url(r.signature),
    },
  };
}

async function send(path: string, body: unknown): Promise<Response> {
  return fetch(`${API_URL}${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

async function parse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

/** An action of the student's half. If the vault has a passkey, the API answers 428 with a challenge:
 *  `askToSign` shows it to the student and resolves once their passkey has signed it. */
export async function studentPost<T = TxResult>(
  path: string,
  body: Record<string, unknown> | undefined,
  askToSign: (ch: PasskeyChallenge) => Promise<Signed>,
): Promise<T> {
  const first = await send(path, body);
  if (first.status !== 428) return parse<T>(first);
  const ch = ((await first.json()) as { passkey: PasskeyChallenge }).passkey;
  const signed = await askToSign(ch);
  return parse<T>(await send(path, { ...body, signed }));
}
