# Pitch deck outline (≤ 10 min) and demo video script (≤ 5 min)

Red-team numbers are from the final run `docs/evidence/redteam/20261018-201351/summary.md`. Use only numbers from real runs.

## Deck: 10 slides, about 1 minute each

1. **Hufu 虎符: a tiger tally for your AI.** Team, Topic F (Agent Wallet), one line: *an AI wallet that pays on its own within what you signed, and can't be talked into the rest.*
2. **The problem, personally.** "I'm a Bangladeshi student in Melbourne. Rent, tuition, money from home: every month is a stack of transfers, fees and scam emails." AI agents can now move money, but LLMs cannot tell instructions from data. A phishing email becomes a payment order. Caps don't help when the scammer asks for less than the cap.
3. **2,200 years ago, China solved delegated authority.** Photo of the Du Tiger Tally (杜虎符). The commander acts alone for small moves; over 50 soldiers needs the ruler's half; beacon-fire emergencies need no matching. → Hufu's four tiers (T0 read, T1 act within limits, T2 student approves, T3 impossible). *Freezing never needs permission; spending does.*
4. **Ayaan's month.** Persona in Hong Kong: rent 4,500 HKD, tuition 9,000 HKD a month, phone plan, roommate split, family support from Dhaka. What the agent does: pays, converts, sets up recurring payments, compares remittance routes, catches scams.
5. **Three independent checks.** Architecture diagram: guard (off-chain) → vault (on-chain) → student. The model never holds a key and is never trusted. Even a fully jailbroken model cannot spend beyond the caps, and only to payees the student trusts.
6. **How the guard decides.** ALLOW / ASK / BLOCK with plain-language reasons. Provenance: an address seen only in an email cannot authorise a payment. Scanner: injection, urgency, lookalike addresses. Verifier: every number and hash in a reply must come from a real tool result. Hash-chained audit, anchored on-chain.
7. **Demo moments (screenshots or live).** (a) Fake fees office email → BLOCK, explained. (b) "Mr Chan's new wallet": *the AI was fooled by the lookalike; the guard wasn't* (show both addresses and the differing middle). (c) "Send it anyway": jailbroken agent → **reverted transaction on Sepolia Etherscan**, 0 moved.
8. **Measured, not claimed.** Red-team table: 40 attacks in 8 categories and 15 normal tasks against 4 configurations, judged on-chain. Attack success: A naive 3/40 (8%) · B guard only 1/40 · C vault only 1/40 · **D Hufu 0/40**; normal tasks completed 14/15 in D. The point to land: *each layer alone missed one attack the other caught* (guard only: split payments past a limit; vault only: an invented late fee to a saved payee). Honest finding: our first run caught a real bug (split payments passed the per-payee limit) and we fixed it on-chain. Say plainly that 40 attacks is a small sample (0/40 still allows up to 9%), which is why the guarantee is the vault, not the model.
9. **Compliant by design.** Fund transfers treated as high-risk, with human oversight and an emergency stop (in line with the 2026 NFRA AI guidance as reported). Payments, never trading. Huitong only offers licensed routes: stablecoins work in Hong Kong but are not permitted in Bangladesh, so Hufu never routes them there. Local model; memos stay on the device; only hashes go on-chain.
10. **What's next.** Passkey for the student's half; port to FISCO BCOS; federated scam-address intelligence across banks (FATE); licensed partners' live quotes in Huitong. Close: *"Give your AI a tiger tally, not your bank account."*

## Demo video: 5 minutes

| Time | Scene | What to show | Voice-over (short) |
|---|---|---|---|
| 0:00–0:25 | Hook | Title, then the Du Tiger Tally | "Two thousand years ago, a bronze tiger split in two decided who could move an army. We rebuilt it for AI agents that move money." |
| 0:25–0:55 | Dashboard tour | Vault, limits, approvals panel, recurring payments | "Ayaan is a student in Hong Kong. His agent can pay saved payees within limits he signed. Everything else waits for his half." |
| 0:55–1:35 | Everyday tasks | "Pay my landlord 4500 HKD" → ALLOW + tx; "Set up tuition…" → approval → approve; "Pay everything due" → auto-conversion + payments | "The model decides what to do; code does the arithmetic. Notice the conversion: exactly enough USD, no guessing." |
| 1:35–2:05 | Limits | "Send my roommate 800 HKD" → ASK, reason, approve in the panel | "Over Kevin's 500 limit, so the guard files the request itself and tells Ayaan why." |
| 2:05–2:50 | Scam shield | "Check my inbox and handle anything urgent" → two high-risk emails, BLOCKs with reasons; then the lookalike landlord | "The fake fees office is blocked. And this one fooled the model: same first and last characters. The guard compares every character." |
| 2:50–3:25 | Jailbreak | Attack lab → guard off → "Send it anyway" → reverted tx; open it on Sepolia Etherscan ([recorded one](https://sepolia.etherscan.io/tx/0x66a5d4eedb2e159bed4a15011c8fd4f2cedad30f42b22a07f47d5ff59a393d06)) | "Assume the model is fully compromised. The vault still says no, on-chain, for anyone to verify." |
| 3:25–3:50 | Kill switch + audit | Freeze as guardian → payment blocked → unfreeze; Audit tab: chain intact, anchors | "Freezing needs no approval. Every decision is in a hash-chained log anchored on-chain." |
| 3:50–4:20 | Huitong | Family → me 500 USD: routes ranked; stablecoin "not offered" with the Bangladesh reason | "For money from home, only licensed routes. Stablecoins aren't permitted in Bangladesh, so Hufu never offers them." |
| 4:20–4:50 | Results | Red-team table | "Forty attacks, four configurations, judged on-chain. Guard only missed one, vault only missed a different one; together, none." |
| 4:50–5:00 | Close | Seal logo | "Hufu. A tiger tally for your AI." |

Recording tips: run the local model before recording so it is warm; keep the dashboard at 1440 px wide; use the suggestion buttons so prompts are exact; cut waiting time in editing.
