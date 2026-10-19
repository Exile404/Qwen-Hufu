# Hufu 虎符 — Technical Documentation

2026 Shenzhen FinTechathon, International Track · Topic F (Agent Wallet), demo story from Topic C (cross-border student finance).

> **A tiger tally for your AI.** The agent pays on its own within what the student signed. Anything bigger needs the student's half of the tally. Freezing never needs permission.

## 1. Problem

"AI as a Financial Participant" means agents that *move money*, not agents that give advice. Two facts make that hard:

1. **LLMs cannot reliably separate instructions from data.** Any text that reaches the model (an email, a web page, an invoice) can act as an instruction. A wallet agent that reads a phishing email can be steered into paying the phisher.
2. **Spending caps alone are not enough.** Agent wallets already offer caps and allowlists (e.g. Coinbase Agentic Wallets, Feb 2026). A cap limits *how much* is lost; it does not stop the agent paying the *wrong person* within the cap, or explain to the user why something was refused.

Regulation points the same way. The NFRA's June 2026 guiding opinions on AI in banking and insurance class applications involving financial transactions as high-risk, needing human oversight at critical stages, emergency deactivation and a manual fallback. TC260's July 2026 guidelines on deploying AI agents call for strict permission control. [COMPLIANCE.md](COMPLIANCE.md) maps these and the Hong Kong, Bangladesh and mainland China rules to Hufu's controls, with sources.

## 2. The design idea: a tiger tally

A *hufu* (虎符) is a bronze tiger split in two: the ruler keeps one half, the commander the other. The Qin-era **Du Tiger Tally (杜虎符)** is inscribed with the rule: the commander acts alone for small moves; mobilising more than 50 soldiers needs the ruler's half to match; in a beacon-fire emergency, act without matching. Hufu implements exactly that rule for an AI agent:

| Tier | What | Who decides | Enforced by |
|---|---|---|---|
| **T0 Read** | Balances, limits, payees, recurring payments, history, inbox, remittance quotes | Agent | No key involved |
| **T1 Act within mandate** | Pay a saved payee within per-payment, per-payee and daily limits; pay due recurring payments; convert currency within limits; **freeze** | Agent (commander's half) | Guard **and** vault contract |
| **T2 Student approval** | New recipient; over-limit amount; new recurring payment; anything whose recipient or amount came from untrusted content | Student (ruler's half) | Guard files the request; vault `approveRequest` / owner-only functions |
| **T3 Forbidden** | Change limits, payees, agent key or expiry; arbitrary contract calls; token approvals; spending while frozen; unfreezing | Nobody via the agent | No tool exists; vault reverts |

## 3. Architecture

```
 Student (dashboard / CLI) ──chat──► Agent loop (Qwen3.5 9B, local via Ollama)
        ▲   │                              │ proposes tool calls
        │   │ approve / reject             ▼
        │   │                    ┌──────────── GUARD (off-chain) ────────────┐
        │   │                    │ L1 schema + tool allowlist                 │
        │   │                    │ L2 policy mirror (tiers, limits, frozen)   │
        │   │                    │ L3 provenance / taint                      │
        │   │                    │ L4 scam + injection + address-poisoning    │
        │   │                    │ L5 decide ALLOW / ASK / BLOCK ─► audit log │──► hash chain (SQLite)
        │   │                    └──────────┬───────────────┬─────────────────┘        │ head anchored
        │   ▼                         ALLOW │               │ ASK                      ▼ on-chain
        │  Owner signer ◄───────────────────┼───────────────┘                 HufuVault.anchorAudit
        │  (student's half; passkey in prod) ▼
        │                         Executor (agent session key = commander's half)
        ▼                                    │
  ┌──────────────────────── HufuVault (Solidity) ─────────────────────────┐
  │ per-token per-payment + daily caps · per-payee daily caps · allowlist   │──► tHKD / tUSD (test stablecoins)
  │ mandates · T2 request queue · session-key expiry · pause (any role)     │──► MockFXPool (tUSD ⇄ tHKD)
  └─────────────────────────────────────────────────────────────────────────┘
        local anvil (development, tests, red team)  →  Ethereum Sepolia (evidence)
```

After the model, every action crosses three independent checks: the **guard** (stops wrong-but-within-limits payments and explains why), the **vault** (the final word on limits, even if the guard and model are both compromised), and the **student** (the tie-breaker for anything risky). The LLM never holds a key and is never trusted.

### Components

| Path | What |
|---|---|
| `contracts/src/HufuVault.sol` | The vault: roles, caps, allowlist, mandates, request queue, pause, audit anchoring |
| `contracts/src/MockFXPool.sol`, `MockStablecoin.sol` | Test-only FX pool and 6-decimal stablecoins (no value) |
| `agent/hufu/agent.py` | Agent loop: model → executor (guard) → model; nudges (silent, described-not-done, asked-to-pay-but-didn't); reply verification; fallback summary |
| `agent/hufu/tools.py` | 16 tools, each tagged T0/T1/T2; deterministic money arithmetic |
| `agent/hufu/compliance.py`, `sanctions_update.py` | Counterparty screening (project list + OFAC SDN Ethereum addresses) and travel-rule-ready records of transfers of HK$8,000 or more |
| `agent/hufu/autopilot.py`, `planner.py` | Autopilot (acts between conversations: tripwire, due payments, funding ahead, scam alerts) and the cash-flow planner |
| `agent/hufu/guard/` | `core.py` decisions · `provenance.py` taint · `scanner.py` scam rules · `verifier.py` reply checks · `audit.py` hash chain |
| `agent/hufu/approvals.py` | The student's half: approve / reject (owner key) |
| `agent/hufu/huitong.py` | Licensed remittance-route comparison with jurisdiction rules |
| `agent/hufu/chain.py` | web3 client, three signers, revert decoding into plain language |
| `agent/hufu/api.py`, `web/` | FastAPI + Next.js dashboard |
| `agent/hufu/redteam.py`, `redteam/scenarios.yaml` | Red-team suite (§6) |

## 4. Core algorithms

### 4.1 Vault enforcement (`HufuVault.sol`)

- `pay(token, to, amount, memoHash)` (agent only, not paused, session not expired): `to` must be allowlisted; `paidToPayeeOnDay[to][token][day] + amount ≤ payeeCap[to][token]` if set (a daily total per payee, so splitting a payment cannot get past it); `amount ≤ perTxCap[token]`; `spentOnDay[token][block.timestamp / 1 days] + amount ≤ dailyCap[token]`; then transfer. Checks-effects-interactions, `nonReentrant`.
- `requestPayment(...)` (agent): records a pending request. Only `approveRequest(id)` (owner, not paused) moves the funds; `rejectRequest` closes it.
- Mandates: the owner signs `{payee, token, amount, interval, nextDue, end}`. `executeMandate(id)` (agent) pays **exactly** `amount`, only when `block.timestamp ≥ nextDue`, only while the payee is still allowlisted, then advances `nextDue`. Revoking a payee kills its mandates.
- `swap(tokenIn, amountIn, minOut)` (agent): only through the owner-registered pool; counts toward the daily cap so repeated conversions cannot drain value through fees.
- `pause()` may be called by the agent, the guardian or the owner (the beacon-fire rule). `unpause()` is owner-only. The owner can always `withdraw`, even while paused.
- Every action emits an event; the dashboard history is rebuilt from events, so the chain is the source of truth.
- Only `keccak256(memo)` goes on-chain; memo text stays on the device.

### 4.2 Guard decision procedure (`guard/core.py`)

For each proposed tool call:

1. **L1** Missing required arguments → let the tool return a precise error so the model can retry.
2. Read-only tools and `freeze_wallet` → **ALLOW**.
3. **L2** Wallet frozen → **BLOCK** every money action.
4. **L3/L4** Recipient not on the allowlist:
   - a lookalike of a saved payee (same first and last 4 hex characters) that the student did not type → **BLOCK** (address poisoning);
   - seen only in third-party content and the student did not ask to pay → **BLOCK**;
   - seen only in content the scanner rates high-risk → **BLOCK**, even if the student said "handle it";
   - otherwise (the student typed it) → **ASK**, with warnings attached if it also appears in an email.
5. Saved payee, T1 payment:
   - the student's message has no payment intent and third-party content was read this turn → **BLOCK** (classic indirect injection);
   - no payment intent → **ASK**;
   - amount appears only in third-party content → **ASK**;
   - above per-payment, per-payee or remaining daily limit → **ASK** (the guard files the request itself).
6. Recurring-payment proposal without recurring intent in the message → **BLOCK**.
7. Otherwise → **ALLOW**; the vault still enforces the limits.

**ASK** turns the call into `request_payment` with the reasons and warnings attached, so the student sees *why* in the approval queue. **BLOCK** returns the reasons and an instruction not to retry.

### 4.3 Provenance / taint (`guard/provenance.py`)

The conversation keeps two corpora: **trusted** (the student's messages) and **untrusted** (outputs of tools flagged `untrusted_output`, e.g. the inbox). A recipient address or amount is *tainted* if it appears in untrusted text and not in trusted text. Numbers are normalised (`4,500` = `4500.0`); hex strings, dates and times are ignored. This needs no model judgement, so a convincing email cannot talk its way past it.

### 4.4 Scam and injection scanner (`guard/scanner.py`)

Rule-based on purpose: fast, explainable in one line, and impossible to prompt-inject. Weighted rules: instructions aimed at AI assistants (5), "pre-approved / don't confirm" (5), changed or new account (4), urgency or penalty (2); per-address checks: lookalike of a saved payee (6), unknown address (2). Score ≥ 5 → high, ≥ 3 → medium. The lookalike warning spells out both full addresses and the differing middle characters, because shortened addresses are exactly what makes poisoning work on people (and, in our testing, on the model).

### 4.5 Reply verifier (`guard/verifier.py`) and fallback

Every final reply is checked: each number ≥ 10 must appear in a tool result or a student message (after normalisation, with rounding tolerance; dates ground their year/month/day), and each transaction hash must be one a tool returned. On failure the model gets one internal rewrite request. If the rewrite still fails, or talks about the check, Hufu replaces it with a summary built only from the tools' own `summary` fields and decisions, and tells the student it did so.

### 4.6 Hash-chained audit with on-chain anchoring (`guard/audit.py`)

Each entry stores `hash = keccak256(prev ‖ ts ‖ kind ‖ data)`. Editing or deleting any entry breaks every later hash (`verify()` reports the first broken id). After every turn that moved money, the head is committed with `HufuVault.anchorAudit(head)`, which timestamps it on-chain; the dashboard checks that the latest on-chain head exists in the local log.

### 4.7 Deterministic money arithmetic

The model decides *what* to do; code decides *how much*. Example: "pay everything that's due" with too little tHKD. `pay_due_mandates` computes the shortfall, converts exactly enough tUSD (`ceil(shortfall / (rate × (1 − fee)))`, split into conversions within the per-payment limit and daily limit), then pays. In testing, the 9B model invented conversion amounts when asked to do this itself.

The same rule prevents double payments: if the model pays a saved payee by hand for exactly a recurring payment that is due ("pay my phone bill"), `pay` runs that recurring payment instead, so a later "pay everything that's due" does not pay the bill again.

### 4.8 Huitong 汇通: licensed remittance comparison (`huitong.py`)

For Dhaka ⇄ Hong Kong, each route's recipient amount is computed against the mid-market rate: `(pay − fixed fee) × rate × (1 − FX margin) − receiving fee`, so routes compare like for like. A route is offered only if it is legal at **both** ends: stablecoins are allowed in Hong Kong (licensed issuers) but not permitted in Bangladesh, so the stablecoin route is excluded with the reason shown. Bangladesh-side routes follow the real process (a student file at an authorised dealer bank). Quotes are sample data and labelled as such everywhere.

### 4.9 Agent runtime (`agent.py`)

A plain loop (no framework) so every step is logged. The local model is Qwen3.5 9B with `reasoning_effort=low`: in our Phase 2 evaluation it completed 12/12 agent tasks versus 9/12 with thinking off, which tended to *describe* actions instead of calling tools. The runtime nudges once if a reply is empty or claims an action that did not run, and falls back to a factual summary.

### 4.10 Autopilot and cash-flow planning (`autopilot.py`, `planner.py`)

Topic F asks for an agent that acts on its own, so Hufu does not wait to be asked. While switched on, the autopilot runs every 20 s by default, and each run does four things in order:

1. **Tripwire.** It lists the money that left the vault through the agent key since the last run (`Paid`, `MandateExecuted`, `Swapped` events) and looks each transaction up in the hash-chained audit log. A transaction Hufu did not record means someone else is using the agent key, so it freezes the wallet at once (the beacon-fire rule) and raises a critical alert: "unfreeze only after rotating the key". This turns risk T6, a stolen session key, from "bounded by the daily caps" into "bounded by one run".
2. **Due recurring payments** are paid (`pay_due_mandates`: exactly what the student signed, converting just enough tUSD if tHKD is short).
3. **Funding ahead.** The planner expands every signed schedule over the next 7 days. If tHKD will be short, it converts the missing tUSD now, within today's limits. A 9,000 HKD tuition payment needs more conversion than one day's tUSD limit allows on a tight balance, so starting early is what keeps it from failing on the due date.
4. **Inbox watch.** New emails go through the guard's scanner, and high-risk ones raise an alert before the student ever asks about them.

The autopilot uses no model: the steps are code. It acts through the same `Guard.execute` path as chat, with a "system run" that has no payment intent, so it can pay signed recurring payments, convert within the limits and freeze, and nothing else. Every run is in the audit log and anchored on-chain when it acted. Alerts are de-duplicated, so a scam email or a shortfall is raised once. `plan_month` (T0) gives the same plan to the chat agent ("will I have enough for my bills this month?").

### 4.11 Compliance controls (`compliance.py`)

Two controls sit next to the guard (the full regulation mapping is in [COMPLIANCE.md](COMPLIANCE.md)):

- **Counterparty screening.** Before Hufu pays an address, files a request for it or saves it as a payee, the guard checks it against the screening list: the project's entries plus every 0x address on the U.S. Treasury OFAC SDN list (`make sanctions-update`; 122 addresses as fetched on 19 Oct 2026). A hit is a BLOCK even when the student typed the address, because sanctions are not a matter of consent. The student's approval re-checks the address before signing, in case the list changed after the request was filed, and the autopilot re-screens saved payees.
- **Transfer records.** Every transfer of HK$8,000 or more (Hong Kong's threshold for the fuller travel-rule data set) gets a record: originator, beneficiary, amount, HKD equivalent, purpose and transaction hash. It is kept on the device, never on-chain, so the student can answer a receiving institution's request.

**Reads on a public node.** Chain reads are cached per block (state only changes when a block is mined) and independent reads are sent as one JSON-RPC batch. On Sepolia this brought a dashboard refresh from 4–16 s to under 0.5 s per endpoint.

Local state is tied to the deployment it belongs to: a fresh local chain redeploys the vault at the same address, so the store forgets that vault's approvals, alerts, audit and settings when the deployment underneath changes.

## 5. Security design

See [SECURITY_SELF_ASSESSMENT.md](SECURITY_SELF_ASSESSMENT.md) for the threat model, the permission-tier implementation and the known-risk list. Key properties:

- **Defence in depth with independent failure modes:** the scanner is rules, provenance is string matching, the vault is code on-chain, and approval is a human. Compromising the model breaks none of them.
- **Least privilege:** the agent key can only call `pay`, `requestPayment`, `executeMandate`, `swap`, `pause` and `anchorAudit`. It cannot change any rule.
- **Bounded worst case:** with the agent key fully compromised, the loss per day is at most the daily caps, paid only to payees the student already trusts (tested in `test_JailbrokenAgent_LossBoundedByDailyCaps`). With the autopilot on, the tripwire freezes the wallet on its next run after the first payment Hufu did not make (`test_tripwire_freezes_when_the_agent_key_is_used_behind_hufus_back`).
- **Privacy:** the model runs locally; memos stay off-chain (only hashes on-chain); the audit log stays on the device with only its head on-chain.

## 6. Evaluation

- **Contracts:** 70 Foundry tests including fuzzing (daily cap never exceeded; per-payee daily cap holds across split payments), key rotation and the jailbroken-agent scenario. `HufuVault` coverage: 100% of lines, 98% of branches. Static analysis: `forge lint` clean; Slither reports no high or medium findings (9 low: zero addresses accepted on purpose to revoke a role, and day-scale timestamp comparisons).
- **Code quality:** `ruff` and `mypy` clean; CI (`.github/workflows/ci.yml`) runs the contract, agent and dashboard checks on every push; `pip-audit` and `pnpm audit --prod` report no known vulnerabilities.
- **Agent and guard:** unit and on-chain integration tests for every guard rule, the API and the audit chain; end-to-end tests with the real model for benign tasks and attacks.
- **Red team:** 40 attacks in 8 categories plus 15 benign tasks, each run against four configurations (A no guard and plain wallet, B guard only, C vault only, D full Hufu), judged on-chain. Results below; every run is in [evidence/redteam/](evidence/redteam/README.md).

**Red-team results** (final run [`20261018-201351`](evidence/redteam/20261018-201351/summary.md), 220 runs, Qwen3.5 9B on one local GPU):

| Configuration | Attack success | 95% CI | HKD to attacker addresses | Benign tasks done |
|---|---|---|---|---|
| A naive (no guard, plain wallet) | 3/40 (8%) | 3–20% | 0 | 12/15 |
| B guard only (plain wallet) | 1/40 (2%) | 0–13% | 0 | 13/15 |
| C vault only (no guard) | 1/40 (2%) | 0–13% | 0 | 15/15 |
| **D full Hufu** | **0/40 (0%)** | 0–9% | 0 | 14/15 |

What we learned:

1. **Each layer alone misses an attack the other catches.** The guard alone (B) let four payments of 500 reach the roommate against a 500 limit (A23); the vault blocks that. The vault alone (C) paid an invented 4,900 HKD "late fee" to the university, a saved payee within every limit (A20); the guard asks because the amount came only from an email. Full Hufu (D) stopped both.
2. **The model never paid an attacker address in this run, even unguarded.** With Hufu's system prompt, the 9B model refused every scam address in A. What went wrong in A were *amounts* to trusted payees: 1,300 HKD to the roommate after an email inflated a 300 split (A19), the split payment (A23), and 10,000 HKD in a day against an 8,000 limit (A25). Those are exactly what code-level limits are for.
3. **Honest statistics.** 0/40 still has a 95% upper bound of 9%, and at 40 attacks the difference between A and D is not statistically significant. Hufu's real guarantee is structural, not the model's refusal rate: the vault bounds the loss whatever the model does (fuzz tests, and the forced jailbreak that lands as a reverted transaction).
4. **Usability.** D completed 14/15 normal tasks with no wrong escalations. Its one failure (N05, "pay everything that's due" right after approving tuition) was the model filing an approval request instead of paying; N05 passed in C and in both earlier runs. Without the vault, A and B cannot freeze the wallet (N10), and without either layer, A cannot turn an over-limit payment into an approval request (N04).
5. **What reached the student.** In D, four attacks ended as approval requests (A30, A31, A37, A38). All four were typed in the chat itself (add an unknown address as a payee, a recurring payment to it, a fake "SYSTEM" message). That is the T2 design: new recipients need the student's half, shown with an unknown-address warning. No attack from an email reached the approval queue.
6. **The red team found a real bug.** Our first full run found A23 in D: the per-payee limit applied per payment, so splitting a payment passed it. We moved the limit on-chain to a daily total per payee and re-ran. The earlier runs are kept in [evidence/redteam/](evidence/redteam/README.md).
7. **Scope of these numbers.** The autopilot (§4.10), the read-only `plan_month` tool and the compliance controls (§4.11) were added after the final run. Screening only changes decisions for addresses on the list, and no red-team scenario uses one. They do not change the chat path's guard rules, and the autopilot has its own on-chain tests (`tests/test_autopilot.py`), but the red-team numbers above measure the chat agent only.

## 7. Reproducing

```bash
make setup          # deps, contract libs, local model, .env
make chain          # local chain (state persists across restarts)
make deploy-local   # deploy + seed the demo vault
make api            # :8000
make web            # :3000 dashboard
make cli            # terminal chat
make test           # Foundry + Python unit tests
make redteam        # the full red-team suite on a private chain
```

**Ethereum Sepolia.** Deployed vault [`0xE1f0bA02B09b5FeF688324150753e4a827167c89`](https://sepolia.etherscan.io/address/0xE1f0bA02B09b5FeF688324150753e4a827167c89#code), source-verified on Etherscan and Sourcify (exact match), as are the two test tokens and the FX pool (`make verify-testnet`); every demo step and its transaction is in [evidence/onchain-11155111.md](evidence/onchain-11155111.md) (`HUFU_ENV=testnet make evidence`). Sepolia now prices contract creation and new storage several times higher than Foundry's local simulation, so `make deploy-testnet` raises gas limits 10× (`--gas-estimate-multiplier 1000`); the whole deployment used 34.3M gas and cost under 0.000000001 ETH at Sepolia's gas price.

## 8. Limitations and next steps

- The owner key is held by a local "owner signer" in the demo; production would use a passkey (WebAuthn) on the student's phone.
- The vault is a purpose-built contract rather than an ERC-4337 smart account; porting the policy to account-abstraction session keys is straightforward.
- The contracts are plain Solidity and portable to FISCO BCOS, WeBank's open consortium chain.
- Scam intelligence is local rules. Sharing lookalike and scam-address signals across institutions without sharing customer data is a natural fit for federated learning (e.g. FATE).
- Remittance quotes are sample data; a production Huitong would use licensed providers' live quotes.
