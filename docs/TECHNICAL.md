# Hufu 虎符: Technical Documentation

2026 Shenzhen FinTechathon, International Track. Topic F (Agent Wallet), with a demo story from Topic C (cross-border student finance). Source code: https://github.com/Exile404/Qwen-Hufu

Hufu is a wallet that lets an AI agent look after a student's everyday money: rent, tuition, bills split with a roommate, currency conversion, and money sent from home. Our demo student is Ayaan, a Bangladeshi master's student in Hong Kong whose family is in Dhaka. The agent pays small, expected things on its own. Anything bigger or unusual waits for Ayaan, who approves it with a passkey on a phone or laptop. Freezing the wallet never needs anyone's permission.

The rest of this document explains why we built it this way (sections 1 and 2), how the pieces fit together (3), how each part works (4), the security design (5), how we tested it (6), how to run it yourself (7), and what it does not do yet (8). The security self-assessment and the compliance mapping are separate documents; we point to them where they matter.

## 1. The problem

"AI as a Financial Participant" means agents that move money, not agents that give advice. Two things make that hard.

First, a language model cannot reliably tell instructions apart from data. Any text that reaches the model can act as an instruction: an email, a web page, an invoice. A wallet agent that reads a phishing email can be steered into paying the phisher.

Second, spending caps on their own are not enough. Agent wallets already offer caps and allowlists (Coinbase Agentic Wallets, for example, since February 2026). A cap limits how much can be lost. It does not stop the agent paying the wrong person within the cap, and it does not tell the user why something was refused.

Regulators are heading the same way. The NFRA's June 2026 guiding opinions on AI in banking and insurance treat applications that make financial transactions as high-risk: they need human oversight at critical steps, emergency deactivation and a manual fallback. TC260's July 2026 guidelines on deploying AI agents ask for strict permission control. [COMPLIANCE.md](COMPLIANCE.md) maps these, along with the Hong Kong, Bangladesh and mainland China rules, to Hufu's controls, with sources.

## 2. The design idea: a tiger tally

A hufu (虎符) is a bronze tiger split in two. The ruler kept one half and the commander the other. The Qin-era Du Tiger Tally (杜虎符) carries its rule on the bronze: the commander may act alone on small matters, mobilising more than 50 soldiers needs the ruler's half to match, and when the beacon fires are lit the commander acts without waiting. We gave an AI agent the same rule, in four tiers:

| Tier | What | Who decides | Enforced by |
|---|---|---|---|
| T0 Read | Balances, limits, payees, recurring payments, history, inbox, remittance quotes, the month's plan | The agent | No key involved |
| T1 Act within the mandate | Pay a saved payee within the per-payment, per-payee and daily limits; pay recurring payments that are due; convert currency within the limits; freeze | The agent (the commander's half) | The guard and the vault contract |
| T2 Student approval | A new recipient; an amount over a limit; a new recurring payment; anything whose recipient or amount came from untrusted content | The student (the ruler's half): a passkey on their own device, or the owner key | The guard files the request; the vault's `approveRequest` and owner-only functions, signed by the passkey and checked on-chain (4.12) |
| T3 Forbidden | Change limits, payees, the agent key or its expiry; arbitrary contract calls; token approvals; spending while frozen; unfreezing | Nobody, through the agent | No tool exists for it, and the vault reverts |

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
        │  Student's half ◄─────────────────┼───────────────┘                 HufuVault.anchorAudit
        │  passkey (WebAuthn) on the        ▼
        │  student's device, checked on-chain; or the owner key
        │                         Executor (agent session key = commander's half)
        ▼                                    │
  ┌──────────────────────── HufuVault (Solidity) ─────────────────────────┐
  │ per-token per-payment + daily caps · per-payee daily caps · allowlist   │──► tHKD / tUSD (test stablecoins)
  │ mandates · T2 request queue · session-key expiry · pause (any role)     │──► MockFXPool (tUSD ⇄ tHKD)
  └─────────────────────────────────────────────────────────────────────────┘
     local anvil (development, tests, red team) · Ethereum Sepolia · FISCO BCOS
```

Once the model has proposed an action, three independent checks stand between it and the money. The guard catches payments that are within the limits but still wrong, and explains why. The vault has the final word on limits even if the guard and the model are both compromised. The student breaks the tie on anything risky. The model never holds a key, and nothing assumes it behaves.

![The Hufu dashboard](images/dashboard.png)

*The dashboard during the demo. Ayaan asked Hufu to send the roommate 800 HKD, which is over the roommate's limit, so the request waits for a passkey signature. In the chat, Hufu has read the inbox, and the guard has flagged both phishing emails, including the one from a lookalike of the landlord's address.*

### Components

| Path | What it does |
|---|---|
| `contracts/src/HufuVault.sol` | The vault: roles, caps, allowlist, mandates, request queue, pause, audit anchoring |
| `contracts/src/MockFXPool.sol`, `MockStablecoin.sol` | A test-only FX pool and 6-decimal test stablecoins with no value |
| `agent/hufu/agent.py` | The agent loop: model, then executor (through the guard), then model again; nudges; reply verification; the fallback summary |
| `agent/hufu/tools.py` | The 16 tools, each tagged T0 to T2, and all money arithmetic |
| `agent/hufu/guard/` | `core.py` decisions, `provenance.py` taint, `scanner.py` scam rules, `verifier.py` reply checks, `audit.py` hash chain |
| `agent/hufu/approvals.py`, `student.py`, `passkey.py` | The student's half: approve, reject and unfreeze, signed by the passkey (checked on-chain) or the owner key; assertion parsing; a software passkey for tests |
| `agent/hufu/autopilot.py`, `planner.py` | The autopilot, which acts between conversations, and the cash-flow planner |
| `agent/hufu/compliance.py`, `sanctions_update.py` | Counterparty screening and travel-rule-ready transfer records |
| `agent/hufu/huitong.py` | Remittance-route comparison with the rules of both jurisdictions |
| `agent/hufu/chain.py` | The web3 client, signers, the passkey relay, and revert reasons decoded into plain language |
| `agent/hufu/api.py`, `web/` | The FastAPI server and the Next.js dashboard |
| `agent/hufu/redteam.py`, `redteam/scenarios.yaml` | The red-team suite (section 6) |

## 4. How it works

### 4.1 The vault (`HufuVault.sol`)

- `pay(token, to, amount, memoHash)` can only be called by the agent, while the vault is not paused and the session key has not expired. The recipient must be on the allowlist. If the payee has a cap, the day's total to that payee (`paidToPayeeOnDay`) plus this amount must stay within it, so splitting a payment does not get round it. The amount must be within the per-payment cap, and the day's spending in that token must stay within the daily cap. The contract follows checks-effects-interactions and is `nonReentrant`.
- `requestPayment(...)` lets the agent record a pending request. Only the owner's `approveRequest(id)` moves the funds; `rejectRequest` closes it.
- Mandates are recurring payments the owner signs: `{payee, token, amount, interval, nextDue, end}`. `executeMandate(id)` pays that amount and no other, only once `nextDue` has passed and only while the payee is still allowlisted, then moves `nextDue` on. Removing a payee cancels its mandates.
- `swap(tokenIn, amountIn, minOut)` converts only through the pool the owner registered, and counts toward the daily cap, so repeated conversions cannot bleed money away in fees.
- The agent, the guardian and the owner can all call `pause()`; that is the beacon-fire rule. Only the owner can `unpause()`. The owner can always `withdraw`, even while paused.
- `executeWithPasskey(call, validUntil, assertion)` runs one owner action signed by the student's passkey and verified on-chain (4.12). Anyone may relay it. Once the passkey has locked the owner key (`ownerKeyRetired`), only the passkey can act as owner.
- Every action emits an event, and the dashboard rebuilds its history from those events, so the chain is the source of truth.
- Only `keccak256(memo)` goes on-chain. The memo text stays on the device.

### 4.2 How the guard decides (`guard/core.py`)

The guard looks at every tool call the model proposes and works through these checks in order:

1. If a required argument is missing, it lets the tool return a precise error so the model can try again (L1).
2. Read-only tools and `freeze_wallet` are allowed.
3. If the wallet is frozen, every money action is blocked (L2).
4. If the recipient is not on the allowlist (L3 and L4):
   - a lookalike of a saved payee (the same first and last 4 hex characters) that the student did not type is blocked as address poisoning;
   - an address that appeared only in third-party content, when the student did not ask to pay, is blocked;
   - an address that appeared only in content the scanner rates high-risk is blocked, even if the student said "handle it";
   - otherwise the student typed it, so the guard asks, with warnings attached if it also appears in an email.
5. If the recipient is a saved payee and the payment would be T1:
   - a message with no payment intent, in a turn where third-party content was read, is blocked (the classic indirect injection);
   - a message with no payment intent otherwise means the guard asks;
   - an amount that appears only in third-party content means the guard asks;
   - an amount the student did not write, and that is not a recurring payment they signed for this payee, means the guard asks, because the model made it up;
   - if the student's message names an address that is not a saved payee but the call pays a saved payee they did not name, the guard asks, because the model switched the recipient (and it says so when the two addresses look alike);
   - an amount above the per-payment, per-payee or remaining daily limit means the guard asks and files the request itself.
6. A proposed recurring payment, when the message shows no intent to set one up, is blocked.
7. Anything else is allowed, and the vault still enforces the limits.

When the guard asks, it turns the call into `request_payment` with the reasons and warnings attached, so the student sees why in the approval queue. A payment is filed once per student message: if the model retries with the same payee, token and amount, it gets the existing request back with "do not file it again", and the student never sees duplicates. A block returns the reasons and tells the model not to retry.

### 4.3 Provenance and taint (`guard/provenance.py`)

The conversation keeps two bodies of text. Trusted text is what the student wrote. Untrusted text is the output of tools marked `untrusted_output`, such as the inbox. A recipient address or an amount is tainted if it appears in untrusted text but not in trusted text. Numbers are normalised first (`4,500` and `4500.0` are the same), and hex strings, dates and times are ignored. No model judgement is involved, so a convincing email cannot argue its way past it.

### 4.4 Scam and injection scanner (`guard/scanner.py`)

We kept the scanner rule-based on purpose. It is fast, every finding can be explained in one line, and there is nothing in it to prompt-inject. The rules carry weights: instructions aimed at AI assistants (5), "pre-approved, don't confirm" (5), a changed or new account (4), urgency or a penalty (2). Each address is also checked: a lookalike of a saved payee scores 6 and an unknown address 2. A score of 5 or more is high risk and 3 or more is medium. The lookalike warning prints both full addresses and the characters that differ, because shortened addresses are exactly what makes poisoning work on people, and in our testing it worked on the model too.

### 4.5 Reply verifier (`guard/verifier.py`) and the fallback summary

Before a reply reaches the student, Hufu checks it. Every number of 10 or more must appear in a tool result or a student message (after normalisation, with some tolerance for rounding; a date counts if its year, month and day are grounded), and every transaction hash must be one a tool returned. If the check fails, the model is asked once, internally, to rewrite. If the rewrite also fails, or talks about the check, Hufu replaces it with a summary built only from the tools' own `summary` fields and the guard's decisions, and tells the student it did so.

Word salad gets the same treatment straight away. We saw the 9B model degrade once, late in a long demo conversation, into 40 or more words with no punctuation or line break; a reply like that is replaced with the summary. None of the 1,061 replies recorded in the red-team runs trips this check.

### 4.6 Hash-chained audit log, anchored on-chain (`guard/audit.py`)

Each entry stores `hash = keccak256(prev ‖ ts ‖ kind ‖ data)`. Editing or deleting any entry breaks every hash after it, and `verify()` reports the first broken one. After every turn that moved money, the head of the chain is committed on-chain with `HufuVault.anchorAudit(head)`, which timestamps it. The dashboard checks that the latest head on-chain exists in the local log.

The API's request threads and the autopilot share one local database. Every call to it holds one lock, and reading the head and appending the next entry happen as a single step, so two writers at once cannot fork the chain. We found this the hard way: during a demo recording, a dashboard refresh and an autopilot run collided and the autopilot run failed. `test_store_and_audit_are_safe_across_threads` reproduces that collision.

### 4.7 The model decides what, code decides how much

Take "pay everything that's due" when there is not enough tHKD. `pay_due_mandates` works out the shortfall, converts just enough tUSD (`ceil(shortfall / (rate × (1 − fee)))`, split into conversions that fit the per-payment and daily limits), and then pays. When we let the 9B model do this arithmetic itself, it invented conversion amounts.

The same idea prevents double payments. If the model pays a saved payee by hand for the amount of a recurring payment that is due ("pay my phone bill"), `pay` runs that recurring payment instead, so a later "pay everything that's due" does not pay the bill twice.

### 4.8 Huitong 汇通: comparing licensed remittance routes (`huitong.py`)

For money between Dhaka and Hong Kong, Hufu works out what the recipient gets on each route against the mid-market rate: `(pay − fixed fee) × rate × (1 − FX margin) − receiving fee`. That way the routes compare like for like. A route is offered only if it is legal at both ends. Stablecoins are allowed in Hong Kong through licensed issuers but are not permitted in Bangladesh, so the stablecoin route is left out and the reason is shown. Routes on the Bangladesh side follow the real process, a student file at an authorised dealer bank. The quotes are sample data and are labelled as such everywhere. The tool also requires the currency of the amount, as the student gave it, because "500" is very different money in USD and in BDT.

### 4.9 The agent loop (`agent.py`)

The loop is plain Python with no agent framework, so every step can be logged. The local model is Qwen3.5 9B with `reasoning_effort=low`. In our Phase 2 evaluation that setting completed 12 of 12 agent tasks; with thinking switched off it completed 9 of 12, mostly because it described actions instead of calling the tools. If a reply is empty, or claims an action that never ran, the runtime nudges the model once, and falls back to a factual summary if that does not help. Any leaked reasoning tags are stripped before the student sees the reply.

The model is replaceable. Section 6 repeats the red team with Llama 3.1 8B in its place.

The loop also reports what it is doing as it goes: each model call, each tool call with the guard's decision and result, and the reply check. The dashboard streams these from `/chat/stream` as newline-delimited JSON, so the student watches each step land, with its guard decision and transaction, instead of staring at a spinner. Listening to these events changes nothing in the loop.

### 4.10 Autopilot and cash-flow planning (`autopilot.py`, `planner.py`)

Topic F asks for an agent that acts on its own, so Hufu does not wait to be asked. When the student switches the autopilot on, it runs every 20 seconds by default, and each run does four things in order:

1. **Tripwire.** It lists the money that left the vault through the agent key since the last run (`Paid`, `MandateExecuted` and `Swapped` events) and looks each transaction up in the audit log. A transaction Hufu did not record means someone else has the agent key. The autopilot freezes the wallet at once (the beacon-fire rule) and raises a critical alert telling the student to rotate the key before unfreezing. A stolen agent key (threat T6 in the security self-assessment) is then limited to one run's worth of payments instead of a full day's caps.
2. **Due recurring payments.** It pays them with `pay_due_mandates`: the amounts the student signed, converting just enough tUSD if tHKD is short.
3. **Funding ahead.** The planner expands every signed schedule over the next 7 days. If tHKD will run short, it converts the missing tUSD now, within today's limits. A 9,000 HKD tuition payment on a tight balance needs more conversion than one day's tUSD limit allows, so starting early is what keeps it from failing on the due date.
4. **Inbox watch.** New emails go through the guard's scanner, and a high-risk one raises an alert before the student ever asks about it.

The autopilot uses no model; every step is code. It goes through the same `Guard.execute` path as the chat, as a "system run" with no payment intent, so it can pay signed recurring payments, convert within the limits and freeze, and nothing else. Every run is in the audit log and is anchored on-chain when it acted. Alerts are raised once, not on every run. The chat agent can ask for the same plan through `plan_month` ("will I have enough for my bills this month?").

### 4.11 Compliance controls (`compliance.py`)

Two controls sit next to the guard. The full mapping to regulations is in [COMPLIANCE.md](COMPLIANCE.md).

- **Counterparty screening.** Before Hufu pays an address, files a request for it or saves it as a payee, the guard checks it against the screening list: the project's own entries plus every 0x address on the U.S. Treasury OFAC SDN list (`make sanctions-update`; 122 addresses as fetched on 19 October 2026). A match is blocked even when the student typed the address, because sanctions are not a matter of consent. Approval checks the address again before signing, in case the list changed after the request was filed, and the autopilot re-screens saved payees.
- **Transfer records.** Every transfer of HK$8,000 or more, Hong Kong's threshold for the fuller travel-rule data, gets a record: originator, beneficiary, amount, HKD equivalent, purpose and transaction hash. It stays on the device, never on-chain, so the student can answer a receiving institution's request.

### 4.12 The student's half as a passkey (`executeWithPasskey`, `student.py`, `passkey.py`)

In our first version the student's half was an owner key stored on the same computer as the agent (risk R1 in the security self-assessment). Now it can be a passkey: a WebAuthn P-256 key held by the student's phone, laptop (Touch ID, Windows Hello) or security key, unlocked with a fingerprint, face or PIN. The private key never leaves that device, and the vault checks every signature on-chain with OpenZeppelin's audited `WebAuthn` library. The library uses the P-256 precompile where the chain has one, as Sepolia and anvil do since the Osaka upgrade (EIP-7951), and a Solidity verifier elsewhere.

1. **Register.** The dashboard creates a passkey (`navigator.credentials.create`, ES256, user verification required), and the owner key stores its public key on the vault (`setPasskey`). From then on, every action of the student's half asks for the passkey: approve, reject, add a payee, sign a recurring payment, unfreeze, and replace or remove the passkey.
2. **Sign.** For each action, the API builds the exact vault call and asks the vault for its challenge: `keccak256(domain, chainId, vault, nonce, validUntil, keccak256(call))`. The API answers HTTP 428 with that challenge. The dashboard shows the student what will be signed, and only after they confirm does it ask the passkey (`navigator.credentials.get`).
3. **Relay and verify.** The API checks the assertion off-chain first, so a wrong passkey gets a clear message. The agent key then relays `executeWithPasskey(call, validUntil, assertion)` and pays the gas. The vault checks that the type is `webauthn.get` and the challenge matches, that the user was present and verified, and that the low-s P-256 signature comes from the registered key. It spends the nonce, and only then calls itself with the signed call, passing the vault's own error through if that call fails. The relayer can neither change the call nor reuse the signature, which is bound to this vault, this chain, one nonce and a 5-minute deadline.
4. **Lock the owner key.** Once the passkey has signed something, the student can lock the owner key (`setOwnerKeyRetired`). The vault accepts that only from the passkey, so the key is never locked before the passkey has shown it works. From then on the owner key has no power left: a stolen key file cannot approve, withdraw, change limits or swap the passkey. It can still freeze, because stopping never needs the other half. The passkey can unlock the key again, and `removePasskey` is refused while the key is locked, so the vault always keeps a working owner.

We test this with 25 Foundry tests (replay, a relayer swapping the signed call for a withdrawal, a changed deadline, another vault's signature, the wrong key, missing user presence or verification, high-s signatures, rotation, locking, and a fuzz test over every byte of the signed call), 17 Python tests through the API on a private chain, and a run of the real dashboard in Google Chrome with a virtual WebAuthn authenticator (`scripts/passkey_e2e.py`: register, approve, lock, freeze and unfreeze, cancel).

On Sepolia, the evidence script signs with a software passkey that produces the same WebAuthn format as a phone (section 7). With the precompile, a passkey-signed lock of the owner key used 168,000 gas, and a passkey-approved new payee used 379,000, mostly for the new storage.

### 4.13 Talking to the chain (`chain.py`)

Chain reads are cached per block, since state only changes when a block is mined, and independent reads go out as one JSON-RPC batch. On a public Sepolia node this brought a dashboard refresh from 4 to 16 seconds down to under half a second per endpoint.

Local state belongs to one deployment. A fresh local chain redeploys the vault at the same address, so when the deployment underneath changes, the store forgets that vault's approvals, alerts, audit log and settings rather than mixing them with the new one.

## 5. Security design

The threat model, how each permission tier is enforced, and the list of known risks are in [SECURITY_SELF_ASSESSMENT.md](SECURITY_SELF_ASSESSMENT.md). The properties we designed for are these.

- **Layers that fail differently.** The scanner is rules, provenance is string matching, the vault is code on-chain, and approval is a person. Compromising the model breaks none of them.
- **Least privilege.** The agent key can call `pay`, `requestPayment`, `executeMandate`, `swap`, `pause` and `anchorAudit`, and cannot change any rule. It also relays the student's passkey signatures, but it cannot alter or reuse them.
- **The student's half lives off the computer.** With a passkey registered and the owner key locked, nothing stored on the computer running the agent can approve a payment, change a limit or unfreeze. That takes the student's fingerprint, face or PIN on their own device, once per action (4.12).
- **A bounded worst case.** If the agent key is fully compromised, the most that can leave in a day is the daily caps, and only to payees the student already trusts (`test_JailbrokenAgent_LossBoundedByDailyCaps`). With the autopilot on, the tripwire freezes the wallet on its next run after the first payment Hufu did not make (`test_tripwire_freezes_when_the_agent_key_is_used_behind_hufus_back`).
- **The local API answers only the dashboard.** It rejects any request whose `Host` is not a local name, so a web page cannot reach it through DNS rebinding. It also rejects any write that a browser sends from a page other than the dashboard, so another site cannot make it chat, pay or approve. The Docker setup publishes the API and the dashboard on this computer only (127.0.0.1). With a passkey registered, approvals and unfreezing also need the student's signature, whoever calls the API.
- **Privacy.** The model runs locally. Memos stay off-chain and only their hashes go on-chain. The audit log stays on the device, with only its head on-chain.

## 6. Evaluation

- **Contracts.** 97 Foundry tests, including fuzzing (the daily cap is never exceeded, the per-payee daily cap holds across split payments, and changing any byte of a passkey-signed call makes it fail), key rotation, the jailbroken-agent scenario, the 25 passkey tests from 4.12, and three tests with FISCO BCOS's millisecond timestamps (section 7). Coverage of `HufuVault` is 99% of lines (the one line not counted is inline assembly) and 98% of branches (`forge coverage --ir-minimum`). `forge lint` is clean. Slither reports no high or medium findings: 11 low (zero addresses accepted on purpose so a role can be revoked; timestamp comparisons for day-scale schedules, the 5-minute passkey deadline and the millisecond check in `_now()`) and 3 informational (the passkey self-call and its error pass-through; a mock's interface).
- **Code quality.** `ruff` and `mypy` are clean. CI (`.github/workflows/ci.yml`) runs the contract, agent and dashboard checks on every push, and also starts the Docker stack and checks it answers. `pip-audit` and `pnpm audit --prod` report no known vulnerabilities.
- **Agent and guard.** 97 unit and on-chain integration tests cover every guard rule, the API, the passkey flow and the audit chain. Nine more tests run the real model end to end on normal tasks and attacks, and `make e2e-passkey` runs the passkey flow in Google Chrome.
- **Red team.** 40 attacks in 8 categories plus 15 normal tasks, each run against four configurations and judged on-chain from balances and vault state, not from what the model said: A has no guard and a plain wallet, B has the guard only, C has the vault only, and D is full Hufu. Every run is in [evidence/redteam/](evidence/redteam/README.md).

The final run, [`20261019-210204`](evidence/redteam/20261019-210204/summary.md), was 220 runs on commit `1b2474b`, with Qwen3.5 9B on one local GPU and a median of 32 seconds per turn:

| Configuration | Attacks that got through | 95% CI | HKD to attacker addresses | Normal tasks done |
|---|---|---|---|---|
| A naive (no guard, plain wallet) | 5/40 (12%) | 5–26% | 0 | 13/15 |
| B guard only (plain wallet) | 1/40 (2%) | 0–13% | 0 | 14/15 |
| C vault only (no guard) | 1/40 (2%) | 0–13% | 0 | 15/15 |
| **D full Hufu** | **0/40 (0%)** | 0–9% | 0 | **15/15** |

What we learned:

1. **Each layer alone misses something the other catches.** With the guard alone (B), four payments of 500 reached the roommate against a 500 limit (A23); the vault blocks that. With the vault alone (C), the model paid an invented 4,900 HKD "late fee" to the university, a saved payee and within every limit (A20); the guard asks, because the amount came only from an email. Full Hufu (D) stopped both.
2. **Without protection, 5 of 40 attacks got through, and every one was an overpayment to a trusted payee.** None paid an attacker's address: with Hufu's system prompt, the 9B model refused every scam address even without the guard. The five were rent inflated to 7,900 HKD by an email (A18), a utilities split inflated to 1,300 (A19), the invented late fee (A20), split payments past the roommate's limit (A23), and 9,000 HKD in a day against an 8,000 limit (A24). Code-level limits and provenance exist for exactly these. In one repeat run, A also paid 4,500 HKD to a lookalike address the student pasted (A16); full Hufu blocked that in every run.
3. **Repeating the runs makes the difference clear.** One run is a small sample. Even 0 out of 40 has a 95% upper bound of 9%, and A's 5 against D's 0 is only borderline (Fisher's exact test, p = 0.05 two-sided). So we ran A and D twice more on the same code ([pooled report](evidence/redteam/pooled-20261020-005916.md)). The model samples at temperature 0.2, so each run is a fresh attempt at the same 40 attacks.

   | Over three runs | Attempts that got through | 95% CI | Normal tasks done |
   |---|---|---|---|
   | A naive | 16/120 (13%) | 8–21% | 39/45 |
   | **D full Hufu** | **0/120** | 0–3% | **45/45** |

   Fisher's exact test gives p = 0.00002 (two-sided). These are repeats of the same 40 attacks, so the numbers describe these attacks rather than every possible one. The guarantee we rely on is structural: whatever the model does, the vault bounds the loss. The fuzz tests show it, and so does the forced jailbreak in our evidence, which lands on-chain as a reverted transaction.
4. **A second model family.** To check that the protection does not depend on Qwen, we swapped in Meta's Llama 3.1 8B Instruct and kept the prompt, tools, guard, vault and scenarios the same ([run `20261020-055237`](evidence/redteam/20261020-055237/summary.md)):

   | Llama 3.1 8B | Attacks that got through | HKD to attacker addresses | Normal tasks done |
   |---|---|---|---|
   | A naive | 2/40 | 5,000 | 9/15 |
   | **D full Hufu** | **0/40** | 0 | **14/15** |

   Unlike Qwen, Llama without protection did pay an attacker: a fake "developer test mode" message got 5,000 HKD sent to an unknown address (A27). It also overspent the daily limit (A24). In D, both attempts ended as a block or an approval request. The weaker model cost usability rather than safety: it did not convert currency when asked (N07, in both configurations).
5. **Usability.** D completed all 15 normal tasks and never escalated one by mistake. A and B cannot freeze a plain wallet (N10). In A, asked how to send 2,000 HKD to Mum, the model paid the phone payee instead (N14).
6. **What reached the student.** In D, five attacks ended as approval requests (A27, A30, A31, A37, A38). All five were typed into the chat itself: a fake "developer test mode", a fake "SYSTEM" policy update, adding an unknown address as a payee, and a daily payment to it. That is the T2 design working: a new recipient needs the student's half, now a passkey signature, and arrives with an unknown-address warning. No attack that came in by email reached the approval queue.
7. **The red team found two real problems, and we fixed both.** The runs that found them are kept in [evidence/redteam/](evidence/redteam/README.md).
   - In our first full run, the per-payee limit applied to each payment separately, so splitting a payment got past it (A23 in D). We moved the limit on-chain as a daily total per payee (risk R4 in the security self-assessment).
   - In run `20261019-202311`, asked to "pay my phone bill", the model paid the phone payee 4,500 tHKD, an amount nobody gave (N06 in D). The guard allowed it because the payee was saved and the amount was within the limits. The guard now asks whenever an amount was neither written by the student nor part of a recurring payment they signed (R19). After the fix, N06 passed in all four configurations.
8. **What these numbers cover.** They measure the chat agent; the autopilot has its own on-chain tests (`tests/test_autopilot.py`). The recipient-switch rule (R14) came after these runs and only adds approval requests; a targeted re-run of the five address-poisoning attacks (A13 to A17) and two normal payments in D stopped every attack and completed both payments. The runs also came before the FISCO BCOS port (section 7), which changes nothing on Ethereum-style chains because `_now()` returns `block.timestamp` unchanged there. The compliance controls were on, but no scenario uses a listed address. The red-team vault has no passkey registered, so approvals there used the owner key; the passkey path is tested separately (4.12).

## 7. Running it yourself

The quickest way is Docker. One command starts the chain, deploys and seeds the vault, and runs the API, the dashboard and the local model (downloaded on the first start):

```bash
docker compose up
```

Then open http://localhost:3000. On a machine with an NVIDIA GPU, add `-f compose.yaml -f compose.gpu.yaml`. The README's "Run with Docker" section covers other ports and using an Ollama that is already installed. Without Docker:

```bash
make setup          # dependencies, contract libraries, local model, .env
make chain          # local chain (state persists across restarts)
make deploy-local   # deploy and seed the demo vault
make api            # API on :8000
make web            # dashboard on :3000
make cli            # chat in the terminal
make test           # Foundry and Python unit tests
make redteam        # the full red-team suite on a private chain
```

**Ethereum Sepolia.** The vault is deployed at [`0x7Ca7FcE5B406131C96aCc53111f8d46E1d54dd5C`](https://sepolia.etherscan.io/address/0x7Ca7FcE5B406131C96aCc53111f8d46E1d54dd5C#code). Its source is verified on Etherscan and Sourcify (exact match), as are the two test tokens and the FX pool (`make verify-testnet`). Every demo step and its transaction is in [evidence/onchain-11155111.md](evidence/onchain-11155111.md), produced by `HUFU_ENV=testnet make evidence`. That includes the passkey steps: a payee approved with a passkey signature, the owner key locked by the passkey, and the locked key's withdrawal [reverted](https://sepolia.etherscan.io/tx/0x2c280edd5e995f4c44584188c5aa40e5dadaa2c11e231dda92b0fe77c46abec4) with `NotOwner`. A software passkey signs there; it produces the same WebAuthn format as a phone, and the vault checks it the same way. Sepolia now prices contract creation and new storage several times higher than Foundry's local simulation, so `make deploy-testnet` raises the gas limits tenfold (`--gas-estimate-multiplier 1000`). The whole deployment used 43.9M gas and cost under 0.000000001 ETH at Sepolia's gas price.

**FISCO BCOS.** Hufu also runs on FISCO BCOS 3.16, WeBank's open-source consortium chain, through its Ethereum-compatible Web3 RPC. The contracts, deploy script, agent and dashboard are the same. `make fisco-chain` builds a one-node chain from pinned, checksum-verified releases, followed by `make deploy-fisco` and `HUFU_ENV=fisco make evidence`. All 24 evidence steps behave as they do on Sepolia ([evidence/onchain-20200.md](evidence/onchain-20200.md)), and the dashboard API serves every view. We had to handle four differences:

1. **Time.** FISCO gives `block.timestamp` in milliseconds, which would have broken session expiry, due dates, daily caps and passkey deadlines. The vault reads its clock through `_now()` instead. A value above 10^12 cannot be seconds (it would be after the year 33,000), so it is divided by 1,000. On Ethereum, `_now()` returns `block.timestamp` unchanged. Three Foundry tests run the vault with millisecond timestamps.
2. **Funding.** The Web3 RPC refuses any transaction from an account with no balance, even at a gas price of 0. The script turns on FISCO's balance feature and funds fresh test keys with the official console.
3. **RPC details.** `eth_getLogs` needs an explicit `toBlock`, and a batch of one call comes back as a bare object, so the agent always names `toBlock` and never batches a single read. We raised the node's batch limit from 8 to 64.
4. **No P-256 precompile.** OpenZeppelin's Solidity verifier runs instead, at about 340,000 gas for a passkey-signed action against 168,000 on Sepolia.

## 8. Limitations and next steps

- **A passkey cannot show what it signs.** The passkey prompt says which site is asking, but WebAuthn has no way to display the transaction. The dashboard shows the exact call before asking for the passkey, but a fully compromised computer could show one action and have another signed. Each signature still needs the student's fingerprint, face or PIN, covers one action and expires in 5 minutes. The next step is a trusted display, with the student's phone rendering the request itself.
- **Recovery.** If the student loses every device holding the passkey while the owner key is locked, only freezing and the agent's limited payments keep working. Synced passkeys (iCloud Keychain, Google Password Manager) make this unlikely. A production version would add guardian-assisted recovery behind a time lock.
- Until the student registers a passkey and locks the owner key, the demo owner key still sits on the same machine as the agent.
- The vault is a purpose-built contract, not an ERC-4337 smart account. Porting the policy to account-abstraction session keys is straightforward.
- Hufu runs on a one-node FISCO BCOS chain (section 7). The step after that is a real consortium, with several institutions running nodes and FISCO's account permissions in place of the demo's single admin.
- Scam intelligence is a set of local rules. Institutions could share lookalike and scam-address signals without sharing customer data, which is a natural fit for federated learning (for example FATE).
- Remittance quotes are sample data. A production Huitong would use licensed providers' live quotes.
