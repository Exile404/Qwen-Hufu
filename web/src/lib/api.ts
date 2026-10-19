export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export type TokenStatus = {
  balance: number;
  per_payment_limit: number;
  daily_limit: number;
  left_today: number;
  enabled: boolean;
};

export type WalletStatus = {
  tokens: Record<string, TokenStatus>;
  frozen: boolean;
  agent_active: boolean;
  agent_session_expires: string;
  chain_time: string;
};

export type Payee = {
  name: string;
  address: string;
  label: string;
  allowed: boolean;
  caps: Record<string, number>;
};

export type Mandate = {
  id: number;
  label: string;
  payee: string;
  token: string;
  amount: number;
  every_days: number;
  next_due: string;
  ends: string;
  active: boolean;
  due_now: boolean;
};

export type Approval = {
  id: number;
  kind: "payment" | "mandate" | "payee";
  payload: Record<string, string | number>;
  reason: string;
  status: "pending" | "approved" | "rejected" | "failed";
  created_at: string;
  tx_hash?: string | null;
  error?: string | null;
};

export type State = {
  vault: string;
  chain_id: number;
  explorer_tx_url: string | null;
  status: WalletStatus;
  payees: Payee[];
  mandates: Mandate[];
  approvals: Approval[];
  guard: { enabled: boolean };
  roles: { owner: string; agent: string; guardian: string | null };
};

export type HistoryItem = {
  kind: string;
  tx_hash: string;
  to?: string;
  from?: string;
  amount?: number;
  token?: string;
  memo?: string;
  label?: string;
  request_id?: number;
  mandate_id?: number;
  by?: string;
};

export type Decision = "ALLOW" | "ASK" | "BLOCK";

export type StepResult = {
  result?: string;
  status?: string;
  summary?: string;
  tx_hash?: string;
  error?: string;
  hint?: string;
  approval_id?: number;
  reasons?: string[];
  emails?: Email[];
  guard?: { decision: Decision; reasons?: string[]; warnings?: string[] };
  [key: string]: unknown;
};

export type Step = {
  tool: string;
  tier: string;
  args: Record<string, unknown>;
  result: StepResult;
  ms: number;
};

export type TurnView = {
  reply: string;
  verifier: string[];
  anchor: { head: string; status: string; tx_hash?: string } | null;
  steps: Step[];
};

export type Email = {
  id: number;
  from: string;
  subject: string;
  date: string;
  body: string;
  lab?: boolean;
  security?: { risk: "high" | "medium" | "low"; findings: string[] };
};

export type AuditEntry = { id: number; ts: string; kind: string; data: Record<string, unknown>; hash: string };

export type AuditView = {
  entries: AuditEntry[];
  verify: { ok: boolean; entries: number; head?: string; broken_at?: number };
  anchors: number;
  last_anchor: { head: string; tx_hash: string; in_local_log: boolean } | null;
};

export type TxResult = { status: string; tx_hash?: string; error?: string; hint?: string; block?: number };

export const short = (s?: string | null, head = 6, tail = 4) =>
  s ? (s.length > head + tail + 1 ? `${s.slice(0, head)}…${s.slice(-tail)}` : s) : "";

export const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

export type AutopilotLevel = "info" | "action" | "warning" | "critical";

export type AutopilotEvent = {
  level: AutopilotLevel;
  kind: string;
  title: string;
  detail?: Record<string, unknown>;
};

export type Alert = AutopilotEvent & { id: number; created_at: string };

export type PlanItem = { label: string; payee: string; token: string; amount: number; due: string };

export type Plan = {
  days: number;
  upcoming: PlanItem[];
  needed: Record<string, number>;
  balances: Record<string, number>;
  hkd_shortfall: number;
  convert_usd: number;
  convert_today_max: number;
  days_to_convert: number;
  covered: boolean;
  note: string;
  summary: string;
};

export type AutopilotStatus = {
  enabled: boolean;
  interval_s: number;
  last_run: { at: string; events: AutopilotEvent[] } | null;
  alerts: Alert[];
  plan: Plan;
};

export type TransferRecord = {
  id: number;
  created_at: string;
  kind: string;
  tx_hash: string | null;
  token: string;
  amount: number;
  hkd_equivalent: number;
  originator: { name: string; wallet: string };
  beneficiary: { name: string; wallet: string };
  purpose: string;
};

export type ComplianceView = {
  screening: { entries: number; list_updated: string | null; ofac_entries: number; ofac_fetched: string | null };
  record_threshold_hkd: number;
  records: TransferRecord[];
};

