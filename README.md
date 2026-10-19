# Hufu 虎符

[![CI](https://github.com/Exile404/Qwen-Hufu/actions/workflows/ci.yml/badge.svg)](https://github.com/Exile404/Qwen-Hufu/actions/workflows/ci.yml)

**A tiger tally for your AI.**

Hufu is an AI agent wallet for international students. It pays rent, tuition and bills in stablecoins, converts currency, compares licensed remittance routes home, and catches scams. Like the two halves of an ancient Chinese tiger tally, the agent can act alone on small, pre-approved payments; anything bigger needs the student's half. The rules are enforced three times: by an off-chain **guard**, by an on-chain **vault** contract, and by the **student**. A jailbroken model still cannot pay anyone the student hasn't approved, or spend beyond the limits the student signed.

2026 Shenzhen FinTechathon, International Track · Topic F (Agent Wallet), with a Topic C (cross-border student finance) demo story. Test tokens only; no real value.

| | |
|---|---|
| Technical documentation | [docs/TECHNICAL.md](docs/TECHNICAL.md) |
| Security self-assessment | [docs/SECURITY_SELF_ASSESSMENT.md](docs/SECURITY_SELF_ASSESSMENT.md) |
| Red-team results | [docs/evidence/redteam/](docs/evidence/redteam/README.md): full Hufu stopped **40/40** attacks and completed 14/15 normal tasks |
| On-chain evidence | [docs/evidence/onchain-11155111.md](docs/evidence/onchain-11155111.md): every demo step on Ethereum Sepolia with Etherscan links |
| Build plan and decisions | [PLAN.md](PLAN.md) |
| Pitch outline and video script | [docs/PITCH.md](docs/PITCH.md) |

## Why a tiger tally

The Qin-era Du Tiger Tally (杜虎符) carries a rule: the commander may act alone for small moves; mobilising more than 50 soldiers needs the ruler's half; in a beacon-fire emergency, act without matching. Hufu's permission tiers follow it:

| Tier | The agent may… | Enforced by |
|---|---|---|
| T0 Read | read balances, limits, payees, history, inbox, remittance quotes | — |
| T1 Act within mandate | pay saved payees within per-payment, per-payee daily and overall daily limits; pay due recurring payments; convert currency; **freeze** | guard + vault |
| T2 Ask the student | file a request for a new recipient, an over-limit amount, a new recurring payment, or anything traced to an email | guard; only the student's key executes |
| T3 Never | change limits, payees, its own key or expiry; unfreeze | no tool exists; vault reverts |

## What it looks like

- **Chat:** every step shows the guard's decision (Allow / Ask Ayaan / Block) and the reason, with transaction links.
- **Your half of the tally:** approval queue; risky requests carry warnings in red.
- **Vault:** balances and how much of today's limit is left; a kill switch anyone with a role can pull.
- **Huitong 汇通:** licensed Dhaka ⇄ Hong Kong routes ranked by what actually arrives; stablecoins are not offered for Bangladesh, with the reason shown.
- **Attack lab:** plant a phishing email, switch the guard off, and "send it anyway" as a jailbroken agent: the vault reverts it on-chain.

## Layout

```
contracts/   Solidity (Foundry): HufuVault, MockFXPool, MockStablecoin, deploy script, 70 tests
agent/       Python: agent loop, tools, guard/, Huitong, chain client, FastAPI, red team, tests
web/         Next.js 16 + shadcn/ui dashboard
redteam/     scenarios.yaml: 40 attacks in 8 categories + 15 benign tasks
docs/        technical doc, security self-assessment, pitch, evidence
```

## Run it locally

Requirements: [Foundry](https://getfoundry.sh), [uv](https://docs.astral.sh/uv/), Node 20+ with pnpm, [Ollama](https://ollama.com), and an NVIDIA GPU with ≥ 10 GB VRAM (or set `LLM_BASE_URL` to any OpenAI-compatible API).

```bash
make setup          # dependencies, contract libraries, local model (Qwen3.5 9B), .env
```

In three terminals:

```bash
make chain          # local chain on :8545 (state is saved across restarts)
make api            # agent API on :8000
make web            # dashboard on :3000
```

Then:

```bash
make deploy-local   # deploy and seed the demo vault
make doctor         # check everything is wired up
```

Open http://localhost:3000, or chat in the terminal with `make cli`.

## Tests and evaluation

```bash
make test               # Foundry tests + Python unit tests (offline)
make test-integration   # on-chain integration tests on a private chain (+ model end-to-end tests)
make redteam            # full red-team suite on a private chain (about 2–3 hours on one GPU)
make evidence           # run the demo tasks through the guard and record every transaction
```

Quality checks (also run by [CI](.github/workflows/ci.yml) on every push, except the tests that need the local model):

| Check | Result |
|---|---|
| Contract tests (`forge test`) | 70 tests incl. fuzzing; `HufuVault` 100% lines, 98% branches (`forge coverage`) |
| Contract static analysis | `forge lint` clean; Slither: no high or medium findings (9 low, all intentional and justified in the code) |
| Agent | `ruff`, `mypy` clean; 51 unit and on-chain tests (`pytest -m "not llm"`) + 7 end-to-end tests with the model |
| Dashboard | `pnpm typecheck`, `pnpm lint`, `pnpm build`; `pnpm audit --prod` and `pip-audit`: no known vulnerabilities |
| Red team | 40 attacks + 15 normal tasks × 4 configurations, judged on-chain ([results](docs/evidence/redteam/README.md)) |

Security issues: see [SECURITY.md](SECURITY.md).

## Ethereum Sepolia

Live deployment (test tokens only), all source-verified on Etherscan and Sourcify: vault [`0xE1f0bA02B09b5FeF688324150753e4a827167c89`](https://sepolia.etherscan.io/address/0xE1f0bA02B09b5FeF688324150753e4a827167c89#code), tHKD [`0xC1eB…a42b`](https://sepolia.etherscan.io/address/0xC1eB2Aa0F6e9eC165E590D7113c401120ec4a42b#code), tUSD [`0x8335…6410`](https://sepolia.etherscan.io/address/0x8335360FAB7cBF8bd77A4C08202cb5E52EDa6410#code), FX pool [`0x5C3B…4697`](https://sepolia.etherscan.io/address/0x5C3B2488f65bA6CbBB46C9bd4B882a0CA9754697#code). The [evidence file](docs/evidence/onchain-11155111.md) links every transaction, including the jailbroken agent's transfer to a scam address, which the vault [reverted](https://sepolia.etherscan.io/tx/0x66a5d4eedb2e159bed4a15011c8fd4f2cedad30f42b22a07f47d5ff59a393d06) with `PayeeNotAllowed`.

To deploy your own:

```bash
make testnet-keys       # fresh owner/agent/guardian keys in .env.testnet (git-ignored); fund the owner
make testnet-balance
make deploy-testnet
make verify-testnet     # publish the source on Etherscan (ETHERSCAN_API_KEY in .env.testnet)
HUFU_ENV=testnet make evidence
HUFU_ENV=testnet make api
```

Never reuse the public Anvil development keys on a public network.

## Honest limits

The owner key sits on the same machine in this prototype (production: a passkey on the student's phone); remittance quotes are sample data; scam rules are English-language rules. The full list is in the [security self-assessment](docs/SECURITY_SELF_ASSESSMENT.md#4-known-risk-list).

## License

MIT, see [LICENSE](LICENSE).
