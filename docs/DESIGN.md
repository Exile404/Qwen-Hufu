# Hufu (虎符) — Design notes and development history

**FinTechathon 2026, International Track. Topic F (Agent Wallet), with Topic C (cross-border student finance) as the demo story.**

> **A tiger tally for your AI.**
> The agent pays on its own within what the student signed. Anything bigger needs the student's half of the tally. Freezing never needs permission.

---

## 0. The name, and why it is the design

A *hufu* (虎符, tiger tally) is a bronze tiger split in two: the ruler keeps one half, the field commander the other. The Qin-era **Du Tiger Tally (杜虎符)**, in the Shaanxi History Museum, carries the rule in its inscription:

| Du Tiger Tally (Qin, 2,200+ years ago) | Hufu wallet |
|---|---|
| The commander moves small forces alone | The agent pays saved payees under the cap, automatically (T1) |
| Mobilising more than 50 soldiers needs the ruler's half to match | Over the cap, a new payee, or a suspicious request needs the student's approval (T2) |
| Beacon-fire emergency: act without matching | Freezing the wallet needs no approval — the agent, a guardian or the student can pause. Only spending needs permission. |

Pitch hook (窃符救赵): when Lord Xinling's men stole the king's half, General Jin Bi held a *matching* tally and still doubted the order, because the situation looked wrong. That is the guard: **a valid key is not enough if the context is suspicious.**

The cross-border feature is named **Huitong (汇通)**, after the Shanxi remittance houses' motto 汇通天下. Their rotating secret codes and drafts burned on payment are the story for the scam-shield slide.

## 1. What we are building

A student gives an AI agent standing instructions in plain language:

> "Pay my 9,000 HKD tuition instalment every month until June. Keep my phone bill paid. Don't spend more than 8,000 HKD a day otherwise."

The agent pays rent, tuition and bills in **test stablecoins (tHKD, tUSD)** from an on-chain vault, converts currencies when HKD runs short, compares licensed remittance routes, warns about scams, and explains every action. What it is *allowed* to do is enforced in three places:

1. **Guard** (off-chain, before anything is signed): permission tiers, provenance/taint, scam and injection scanning.
2. **HufuVault contract** (on-chain, final word): caps, payee allowlist, mandates, session-key expiry, pause.
3. **Student approval** for anything risky, signed with a passkey on the student's own device and verified by the vault on-chain.

**Persona:** Ayaan, a Bangladeshi master's student in **Hong Kong**. His family in Dhaka sends USD through a licensed remittance provider; it lands in HK and is held as a USD stablecoin. Rent, tuition and bills are paid in HKD.

**Jurisdiction-aware by design.** Hong Kong licenses stablecoin issuers, so HK-side payments run on-chain. Mainland China bans crypto trading, and Bangladesh does not permit crypto, so Hufu **never** routes money to those places by stablecoin: for Dhaka↔HK the Huitong module compares *licensed* remittance routes and says why. This is payments and treasury, **never trading or speculation.** The pitch opens with the founder's own experience as a Bangladeshi student in Melbourne.

### Positioning: caps are now table stakes

Coinbase launched Agentic Wallets (Feb 2026) with session caps and per-transaction limits; Privy, Crossmint and several open-source projects offer caps and allowlists too. "It can't spend more than the cap" is no longer novel. What Hufu adds:

| Others | Hufu |
|---|---|
| Caps stop *how much* | Taint tracking + scam shield stop *paying the wrong person* even within the cap |
| Policy as config | Tiers that match the NFRA's 2026 guidance on high-risk AI in financial transactions: human oversight, emergency stop, manual fallback ([COMPLIANCE.md](COMPLIANCE.md)) |
| Claims | Measured red-team results with and without each layer |
| Generic agent | A real student workflow: rent, tuition, remittances, FX |

### How it maps to the judging criteria

| Criterion | Weight | What Hufu shows |
|---|---|---|
| Task completion | 40% | The tasks in §3 end to end, each with a transaction hash or a logged refusal, plus the autopilot (pays due bills, funds them ahead, watches the inbox); 15/15 normal tasks in the final red-team run; every demo step on Ethereum Sepolia ([evidence](evidence/onchain-11155111.md)) |
| Security & compliance | 30% | Four tiers enforced in code and on-chain, taint tracking, kill switch, stolen-key tripwire, hash-chained audit anchored on-chain, sanctions screening, travel-rule-ready records; red team: full Hufu stopped 40/40 attacks (§5.4) |
| Innovation & interaction | 30% | Tiger-tally design; live jailbreak where the contract still says no; scam shield; Huitong remittance comparison |
| On-chain bonus | 0–5 | Source-verified contracts on Sepolia; every step's transaction hash, including the jailbroken agent's transfer that the vault reverted |

---

## 2. Permission tiers

| Tier | What | Who decides | Enforced by |
|---|---|---|---|
| **T0 Read** | Balances, policy, payees, mandates, history, inbox, quotes, scam check | Agent, automatic | No signing key involved |
| **T1 Act within mandate** | Pay a saved payee ≤ per-tx, per-payee and daily caps; execute a due mandate; swap ≤ cap via the registered FX pool; **pause** | Agent, automatic | Guard **and** vault |
| **T2 Student approval** | New payee; over-cap amount; create/change a mandate; any payment whose payee or amount came from untrusted content; high scam score | Student approves in the dashboard (their passkey signs; the vault verifies it on-chain) | Guard queues it; vault `approveRequest` / owner calls execute |
| **T3 Forbidden** | Change caps/policy/agent key, arbitrary contract calls, token approvals, spending while paused or after session expiry, unpausing | Nobody via the agent: no tool exists | Vault reverts |

---

## 3. Tasks the agent completes (task-completion evidence)

| # | Task | Tier | Evidence |
|---|---|---|---|
| 1 | "How much can I still spend today?" | T0 | Log |
| 2 | Pay October rent (4,500 tHKD) to the landlord | T1 | Tx hash |
| 3 | Set up the tuition mandate (9,000 tHKD monthly until June) | T2 → T1 | Owner `createMandate` tx |
| 4 | **Multi-step:** "Pay my tuition" → not enough tHKD → convert 500 tUSD → execute the due mandate | T1 | Swap tx + mandate tx |
| 5 | Execute the phone-plan mandate | T1 | Tx hash |
| 6 | Split utilities with the roommate: 300 tHKD (T1); 800 tHKD exceeds his 500 cap → T2 | T1/T2 | Tx hash + request |
| 7 | **Huitong:** "Mum is sending me 500 USD — which route is cheapest?" and "Can she just send stablecoins?" → compares licensed routes (fee + FX spread); explains stablecoins are not permitted in Bangladesh | T0 | Comparison log |
| 8 | **Scam shield:** inbox email "University fee office: our bank account changed, pay to 0x…" → flagged, refused, explained; if the student insists → T2 with warning → rejected | T0/T2 | Guard log + rejected request |
| 9 | **Address poisoning:** lookalike of the landlord's address (same first/last characters) → blocked | Guard | Guard log |
| 10 | **Kill switch:** "Freeze my wallet" / dashboard button / guardian → vault paused | T1 (pause) | Pause tx |
| 11 | **Live jailbreak:** guard switched off, model told to drain the vault → contract reverts | Vault | **Reverted tx hash** |

---

## 4. Architecture

```
 Student (dashboard) ──chat──►  Agent loop (Qwen3.5 9B, local via Ollama)
        ▲   │                        │ proposes tool calls
        │   │ approve / reject       ▼
        │   │                  ┌──────────── GUARD ─────────────┐
        │   │                  │ L1 schema + tool allowlist      │
        │   │                  │ L2 policy mirror (tiers, caps)  │
        │   │                  │ L3 provenance / taint           │
        │   │                  │ L4 scam + injection scanner     │
        │   │                  │ L5 decide: ALLOW / ASK / BLOCK  │──► audit log (hash-chained, SQLite)
        │   │                  └───────┬───────────────┬─────────┘          │ head anchored on-chain
        │   ▼                    ALLOW │               │ ASK                ▼
        │  Owner signer ◄──────────────┼───────────────┘            HufuVault.anchorAudit()
        │  (student's half of the      ▼
        │   tally; passkey)         Executor (agent session key = commander's half)
        ▼          │                   │
   ┌──────────────── HufuVault (Solidity) ─────────────────┐
   │ caps · payee allowlist · mandates · expiry · pause      │──► MockStablecoin tHKD / tUSD
   │ pay · requestPayment · approveRequest · executeMandate  │──► MockFXPool (tUSD ⇄ tHKD)
   └─────────────────────────────────────────────────────────┘
        local anvil (dev)  →  Ethereum Sepolia testnet (evidence)
```

**Key principle:** the LLM never holds a key and is never trusted. The guard is the first line, the contract the last, the student the tie-breaker.

**Output verifier:** every number in the agent's reply must appear in a tool output or the user's message; otherwise the reply is flagged and regenerated once.

**Privacy:** memos stay off-chain; only `keccak256(memo)` goes on-chain. The model runs locally, so no personal data leaves the device.

---

## 5. Components

### 5.1 Contracts (`contracts/`, Solidity 0.8.28, Foundry, OpenZeppelin 5.6)
- `MockStablecoin` — 6-decimal test tokens tHKD, tUSD. ✅
- `MockFXPool` — fixed-rate tUSD⇄tHKD pool with a fee (sample rate 7.80 HKD/USD, owner-set).
- `HufuVault` — one per student:
  - roles: `owner` (student), `agent` (session key + expiry), `guardian` (can only pause)
  - per-token `perTxCap`, `dailyCap`, UTC-day spend buckets; per-payee daily cap and label
  - `pay` (T1), `requestPayment` → `approveRequest` / `rejectRequest` (T2)
  - mandates: owner creates `{payee, token, amount, interval, nextDue, end}`; agent can only `executeMandate(id)` when due, for exactly that amount, while the payee is still allowed
  - `swap` via the registered pool only, ≤ per-tx cap, counted toward the daily cap
  - `pause` (agent, guardian or owner), `unpause` (owner only)
  - `anchorAudit(bytes32)`; events for everything (the dashboard history reads them)

### 5.2 Agent (`agent/hufu/`, Python 3.12, FastAPI, web3.py, OpenAI-compatible client)
- `llm.py` ✅ · `config.py` ✅ · `api.py` ✅ (health only so far)
- `chain.py` contract bindings, executor (agent key), owner signer (owner key) and passkey relay; `student.py`, `passkey.py` the student's half as a WebAuthn passkey
- `tools.py` tool schemas + implementations (T0/T1/T2)
- `guard/` ✅ `core.py` (ALLOW/ASK/BLOCK decisions), `provenance.py` (taint), `scanner.py` (scam/injection/address poisoning), `verifier.py` (numbers + tx hashes in replies), `audit.py` (hash chain, anchored on-chain)
- `agent.py` loop: model → guard → execute/queue/refuse → model
- `huitong.py` ✅ licensed remittance-route comparison + jurisdiction rules (sample quotes, clearly labelled; stablecoin excluded for Bangladesh and mainland China)
- `scam.py` rules + address-poisoning check (+ optional LLM classifier)
- `data/seed/` persona, payees, inbox (benign + phishing), quotes

### 5.3 Dashboard (`web/`, Next.js 16, shadcn/ui)
Chat · balances & remaining caps · mandates · **approval queue** · audit timeline with explorer links · kill switch · "attack lab" (inject a phishing email, switch the guard off for the jailbreak demo).

### 5.4 Red team (`redteam/`)
~40 attacks in 8 categories + ~15 benign tasks, against 4 configurations:

| Config | Guard | Vault | Purpose |
|---|---|---|---|
| A naive | off | off (agent holds an unrestricted key) | What most agent wallets look like |
| B guard only | on | off | Value of the guard alone |
| C vault only | off | on | Loss is bounded by caps, but scams inside the cap succeed |
| D full | on | on | Hufu |

Categories: indirect injection via inbox · payee substitution · address poisoning · amount inflation · cap evasion by splitting · direct jailbreak in chat · injection via memo/token metadata · policy-tampering requests.
Metrics: attack success rate (funds reached attacker, judged on-chain), funds lost, benign task success, false-escalation rate, latency. **Only numbers from real runs go in the report.**

---

## 6. Development history

Each phase was built, then tested by hand and by automated tests, and fixed before the next one started.

| Phase | What was built | How it was verified |
|---|---|---|
| **P0 Scaffold** | Repo, toolchain, local model, smoke tests | `make doctor`, `make test` |
| **P1 Contracts** | `HufuVault`, `MockFXPool`, test stablecoins, deploy and seed script | Foundry tests (now 97, including fuzzing); local deployment |
| **P2 Agent core** | Chain client, tools, agent loop, CLI chat | The everyday tasks run end to end in the CLI |
| **P3 Guard** | Tiers, taint, scanner, lookalike detection, reply verifier, hash-chained audit with on-chain anchoring, kill switch | Hand-run attacks plus integration tests for every rule |
| **P4–P5 Dashboard and student features** | API, Next.js dashboard, approvals, Huitong, attack lab | Click-through of the full demo story |
| **P6 Red team** | 40 attacks and 15 normal tasks against four configurations, judged on-chain | The first full run found a real gap (split payments passed the per-payee limit); fixed in the contract and re-run: full Hufu 0/40 ([results](evidence/redteam/README.md)) |
| **P7 Testnet** | Ethereum Sepolia deployment and evidence capture | Every step's transaction on Etherscan; contracts source-verified |
| **P8 Hardening** | Autopilot (tripwire, due payments, funding ahead, scam alerts), cash-flow planner, compliance controls (sanctions screening, transfer records), CI | Autopilot and compliance tests on a private chain; CI on every push |
| **P9 Passkey** | The student's half as a WebAuthn passkey, verified on-chain (`executeWithPasskey`); owner key lockable; dashboard sign-and-confirm flow | 25 Foundry tests incl. fuzzing; API tests on a private chain; the dashboard in Google Chrome with a virtual authenticator (`make e2e-passkey`); passkey-signed transactions on Sepolia |
| **P10 Final red team** | A re-run on the passkey release found the model inventing an amount for a saved payee (N06); the guard now asks unless the amount came from the student or a signed recurring payment | Final run on the submitted code: full Hufu 0/40 attacks and 15/15 normal tasks ([results](evidence/redteam/20261019-210204/summary.md)) |
| **P11 FISCO BCOS** | One-command one-node FISCO BCOS 3.16 chain with its Web3 RPC; the vault reads FISCO's millisecond timestamps as seconds | All 24 evidence steps on FISCO BCOS ([evidence](evidence/onchain-20200.md)); Foundry tests with millisecond timestamps |

## 7. Stack decisions

| Choice | Why |
|---|---|
| Qwen3.5 9B via Ollama, local (`hufu-qwen`, 16K ctx), `reasoning_effort=low` | Phase 2 eval, 3 runs × 4 agent tasks: **12/12 with low vs 9/12 with thinking off** (off narrates actions instead of calling tools). Median 32 s per turn in the final red-team run on one local GPU. Local = no personal data leaves the device. Any OpenAI-compatible API can be swapped in via `.env`. |
| Anvil for dev, Ethereum Sepolia for evidence, FISCO BCOS for WeBank's stack | Instant local loop; public explorer for judges. The same contracts run on FISCO BCOS (WeBank's chain) as a next step. |
| Custom vault instead of a full ERC-4337 smart account | Small, fully tested (99% line coverage), easy to explain and to audit. |
| Simple explicit agent loop (no framework) | Every step visible and logged, which the security report needs. The runtime nudges the model if it goes silent, claims an action it didn't take, or was asked to pay and didn't call a payment tool, and falls back to a factual summary of what ran. |
| Deterministic money arithmetic in tools | The model decides *what*; code decides *how much*. E.g. `pay_due_mandates` converts exactly the tUSD needed. The 9B model invented conversion amounts when asked to do it itself. |
| The student's half as a passkey (WebAuthn), checked on-chain with OpenZeppelin's `WebAuthn` library | Keeps the student's half off the computer running the agent; works with phones, Touch ID, Windows Hello and security keys without a wallet app; the P-256 precompile (EIP-7951) keeps the signature check cheap on Sepolia. The owner key bootstraps the vault and can then be locked. |

### Guard decision rules (as built)

| Situation | Decision |
|---|---|
| Read-only tool, or freezing the wallet | ALLOW |
| Wallet frozen, any money action | BLOCK |
| Recipient on the screening list (OFAC SDN or the project's list) | BLOCK, even if the student typed it |
| Recipient is a lookalike of a saved payee and the student didn't type it | BLOCK (address poisoning) |
| Recipient address seen only in third-party content, student didn't ask to pay | BLOCK |
| …student did ask, but the source is rated high-risk by the scanner | BLOCK |
| New recipient the student typed themselves | ASK (filed as a request, with warnings if it also appears in an email) |
| Saved payee, but over per-payment / per-payee / daily limit | ASK (auto-filed) |
| Saved payee, amount came only from third-party content | ASK |
| Payment the student didn't ask for (no pay/send/… in the message) | ASK, or BLOCK if third-party content was read this turn |
| Recurring-payment proposal the student didn't ask for | BLOCK |
| Everything else | ALLOW; the vault still enforces caps |

Every reply is checked by the verifier (numbers and tx hashes must come from tool results or the student); one rewrite is requested, and if problems remain the reply is replaced by a factual summary of what actually ran. Every turn that moved money anchors the audit head on-chain.

## 8. Known risks

The maintained list (R1–R17, with severity and mitigation) is in [SECURITY_SELF_ASSESSMENT.md §4](SECURITY_SELF_ASSESSMENT.md#4-known-risk-list).
