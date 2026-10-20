# Hufu 虎符

[![CI](https://github.com/Exile404/Qwen-Hufu/actions/workflows/ci.yml/badge.svg)](https://github.com/Exile404/Qwen-Hufu/actions/workflows/ci.yml)

**A tiger tally for your AI.**

Hufu is an AI agent wallet for international students. It pays rent, tuition and bills in stablecoins, converts currency, compares licensed remittance routes home, and catches scams. Like the two halves of an ancient Chinese tiger tally, the agent can act alone on small, pre-approved payments; anything bigger needs the student's half, signed with a **passkey on their own phone or laptop and verified by the vault on-chain**. The rules are enforced three times: by an off-chain **guard**, by an on-chain **vault** contract, and by the **student**. A jailbroken model still cannot pay anyone the student hasn't approved, or spend beyond the limits the student signed.

2026 Shenzhen FinTechathon, International Track · Topic F (Agent Wallet), with a Topic C (cross-border student finance) demo story. Test tokens only; no real value.

| | |
|---|---|
| Run it | `docker compose up`, then open http://localhost:3000 ([details](#run-with-docker-one-command)) |
| Technical documentation | [docs/TECHNICAL.md](docs/TECHNICAL.md) |
| Security self-assessment | [docs/SECURITY_SELF_ASSESSMENT.md](docs/SECURITY_SELF_ASSESSMENT.md) |
| Compliance mapping | [docs/COMPLIANCE.md](docs/COMPLIANCE.md): HK, mainland China, Bangladesh and FATF rules mapped to Hufu's controls, with sources |
| Red-team results | [docs/evidence/redteam/](docs/evidence/redteam/README.md): full Hufu stopped **all 40 attacks in each of three runs (0/120)** and completed every normal task; with no protection, 16/120 got through (p = 0.00002). With a second model family (Llama 3.1 8B), full Hufu again stopped 40/40 |
| On-chain evidence | [docs/evidence/onchain-11155111.md](docs/evidence/onchain-11155111.md): every demo step on Ethereum Sepolia with Etherscan links |
| Design notes and development history | [docs/DESIGN.md](docs/DESIGN.md) |
| Third-party licences | [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) |
| Pitch outline and video script | [docs/PITCH.md](docs/PITCH.md) |

## Why a tiger tally

The Qin-era Du Tiger Tally (杜虎符) carries a rule: the commander may act alone for small moves; mobilising more than 50 soldiers needs the ruler's half; in a beacon-fire emergency, act without matching. Hufu's permission tiers follow it:

| Tier | The agent may… | Enforced by |
|---|---|---|
| T0 Read | read balances, limits, payees, history, inbox, remittance quotes | — |
| T1 Act within mandate | pay saved payees within per-payment, per-payee daily and overall daily limits; pay due recurring payments; convert currency; **freeze** | guard + vault |
| T2 Ask the student | file a request for a new recipient, an over-limit amount, a new recurring payment, or anything traced to an email | guard; only the student's half executes (a passkey signature the vault verifies) |
| T3 Never | change limits, payees, its own key or expiry; unfreeze | no tool exists; vault reverts |

## What it looks like

![The Hufu dashboard: the guard flags a lookalike-address email, the agent refuses it, an over-limit payment waits for the student's passkey](docs/images/dashboard.png)

<sub>The real dashboard on a local chain. Asked to pay rent to a "new wallet" from an email, Hufu flags the lookalike address and refuses. An over-limit payment to the roommate waits in the student's half of the tally, to be approved with a passkey.</sub>

- **Chat:** every step streams in as it runs, showing the guard's decision (Allow / Ask Ayaan / Block), the reason and the transaction link.
- **Your half of the tally:** approval queue; risky requests carry warnings in red.
- **Your half, on your device:** register a passkey (phone, Touch ID, Windows Hello or a security key). Every approval then shows exactly what will be signed, your fingerprint, face or PIN signs it, and the vault checks the signature on-chain. Lock the owner key and the key file on the computer has no power left; freezing still needs nobody's permission.

  <img src="docs/images/passkey-dialog.png" alt="Sign with your passkey: Approve: Pay 800 tHKD to roommate" width="420">
- **Vault:** balances and how much of today's limit is left; a kill switch anyone with a role can pull.
- **Huitong 汇通:** licensed Dhaka ⇄ Hong Kong routes ranked by what actually arrives; stablecoins are not offered for Bangladesh, with the reason shown.
- **Autopilot:** acts between chats. It pays signed bills when due, converts currency ahead so the next week's bills are covered, flags scam emails, and freezes the wallet if the agent key moves money Hufu did not send. A 30-day cash-flow plan sits on the same card.
- **Compliance:** every new recipient is screened against a sanctions list (blocked even if you insist); transfers of HK$8,000 or more get a travel-rule-ready record on the device.
- **Attack lab:** plant a phishing email, switch the guard off, "send it anyway" as a jailbroken agent (the vault reverts it on-chain), or use a "stolen" agent key behind Hufu's back and watch the autopilot's tripwire freeze the wallet.

## Layout

```
contracts/   Solidity (Foundry): HufuVault, MockFXPool, MockStablecoin, deploy script, 97 tests
agent/       Python: agent loop, tools, guard/, Huitong, chain client, FastAPI, red team, tests
web/         Next.js 16 + shadcn/ui dashboard
redteam/     scenarios.yaml: 40 attacks in 8 categories + 15 benign tasks
docs/        technical doc, security self-assessment, pitch, evidence
```

## Run with Docker (one command)

Needs only [Docker](https://docs.docker.com/get-docker/) with Compose.

```bash
docker compose up
```

Then open **http://localhost:3000**. Compose starts a private test chain, deploys and seeds the vault (10,000 tHKD and 800 tUSD of test tokens, four saved payees, a recurring phone bill), and runs the agent API and the dashboard.
- The first start builds the images (a few minutes). It also downloads the local model, Qwen3.5 9B (about 6.6 GB), into a Docker volume. Follow the download with `docker compose logs -f model`.
- Everything except the chat works before the model arrives: the passkey, approvals, the attack lab, the autopilot, compliance and the audit log.
- `docker compose down` stops it; add `-v` to also delete the downloaded model.

Running the model:
- **NVIDIA GPU** (needs the NVIDIA Container Toolkit): `docker compose -f compose.yaml -f compose.gpu.yaml up`. Replies take 5–30 s.
- **CPU only:** works, but each reply can take minutes, and Docker needs about 10 GB of memory.
- **Ollama already installed on your machine** (fastest on a Mac): run `ollama pull qwen3.5:9b && ollama create hufu-qwen -f agent/Modelfile`, make Ollama listen on all interfaces (`OLLAMA_HOST=0.0.0.0 ollama serve`; on a Mac, Docker Desktop reaches it without this), then:

  ```bash
  HUFU_LLM_BASE_URL=http://host.docker.internal:11434/v1 docker compose up chain deploy api web
  ```

Ports 3000 and 8000 busy? Use `HUFU_WEB_PORT=3010 HUFU_API_PORT=8010 docker compose up`. The chain uses Foundry's public Anvil development keys, which are fine for a private test chain only.

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

**Passkey.** In the dashboard, "Set up passkey" creates one and stores its public key on the vault. Use `localhost`, not `127.0.0.1` (passkeys need a domain name). Any passkey works:
- a phone: choose "use a phone or tablet" and scan the QR code;
- Touch ID or Windows Hello;
- a security key;
- with no authenticator at all, Chrome DevTools' virtual one (More tools › WebAuthn).

Approvals then ask for the passkey. "Lock owner key" makes it the only way to act as the student.

## Tests and evaluation

```bash
make test               # Foundry tests + Python unit tests (offline)
make test-integration   # on-chain integration tests on a private chain (+ model end-to-end tests)
make redteam            # full red-team suite on a private chain (about 2–3 hours on one GPU)
make e2e-passkey        # the passkey flow in Chrome with a virtual authenticator (no model needed)
make evidence           # run the demo tasks through the guard and record every transaction
```

Quality checks (also run by [CI](.github/workflows/ci.yml) on every push, except the tests that need the local model):

| Check | Result |
|---|---|
| Contract tests (`forge test`) | 97 tests incl. fuzzing, 25 passkey tests and FISCO BCOS millisecond timestamps; `HufuVault` 99% lines (the one uncounted line is inline assembly), 98% branches (`forge coverage --ir-minimum`) |
| Contract static analysis | `forge lint` clean; Slither: no high or medium findings (11 low and 3 informational, all intentional and justified in the code) |
| Passkey in a browser | `make e2e-passkey`: register, approve, lock the owner key, unfreeze and cancel in Google Chrome with a virtual WebAuthn authenticator |
| Agent | `ruff`, `mypy` clean; 93 unit and on-chain tests (`pytest -m "not llm"`) + 9 end-to-end tests with the model |
| Dashboard | `pnpm typecheck`, `pnpm lint`, `pnpm build`; `pnpm audit --prod` and `pip-audit`: no known vulnerabilities |
| Red team | 40 attacks + 15 normal tasks × 4 configurations, judged on-chain ([results](docs/evidence/redteam/README.md)) |

Security issues: see [SECURITY.md](SECURITY.md).

## Ethereum Sepolia

Live deployment (test tokens only), all source-verified on Etherscan and Sourcify: vault [`0x7Ca7FcE5B406131C96aCc53111f8d46E1d54dd5C`](https://sepolia.etherscan.io/address/0x7Ca7FcE5B406131C96aCc53111f8d46E1d54dd5C#code), tHKD [`0x86E3…6E3E`](https://sepolia.etherscan.io/address/0x86E39651971AD255370fb91278110D4881EF6E3E#code), tUSD [`0xF476…4535`](https://sepolia.etherscan.io/address/0xF476591954cB4f8695d723F81143Ea4910Dc4535#code), FX pool [`0xDF27…C1Ae`](https://sepolia.etherscan.io/address/0xDF274c2F136e6E9295fBF7a40608DDF4b90eC1Ae#code). The [evidence file](docs/evidence/onchain-11155111.md) links every transaction, including the jailbroken agent's transfer to a scam address, which the vault [reverted](https://sepolia.etherscan.io/tx/0xd46afcc7ed6a308e953c66364d548e1e32b952b171189a6bfda775fcb48eeddd) with `PayeeNotAllowed`. It also has the passkey steps: a payee [approved with a passkey signature](https://sepolia.etherscan.io/tx/0x9f585092f4263dc740227dec7ee3059772a7fe090ad42c8fa8b61a1ee793a5ff) that the vault verified on-chain, and, once the passkey had locked the owner key, the owner key's withdrawal [refused](https://sepolia.etherscan.io/tx/0x2c280edd5e995f4c44584188c5aa40e5dadaa2c11e231dda92b0fe77c46abec4) with `NotOwner`.

To deploy your own:

```bash
make testnet-keys       # fresh owner/agent/guardian keys in .env.testnet (git-ignored); fund the owner
make testnet-balance
make deploy-testnet
make verify-testnet     # publish the source on Etherscan (ETHERSCAN_API_KEY in .env.testnet)
HUFU_ENV=testnet make evidence
make testnet-topup      # refill the vault with test tokens for a fresh demo
HUFU_ENV=testnet make api   # then `make web`: the dashboard runs on Sepolia, with Etherscan links
```

The dashboard works the same on Sepolia; payments take about one block (12 s) to confirm. Reads are cached per block and sent as JSON-RPC batches, so each dashboard refresh costs only a few calls to the public node.

Never reuse the public Anvil development keys on a public network.

## FISCO BCOS

Hufu also runs on [FISCO BCOS](https://github.com/FISCO-BCOS/FISCO-BCOS) 3.16, WeBank's open consortium chain, through its Ethereum-compatible Web3 RPC: the same contracts, agent and dashboard. All 24 evidence steps pass there too ([evidence](docs/evidence/onchain-20200.md)), including the passkey checked on-chain.

```bash
make fisco-chain              # one-node chain from pinned, checksum-verified releases (first run ~300 MB; Java 8+)
make deploy-fisco             # the same Foundry deploy script
HUFU_ENV=fisco make evidence  # or HUFU_ENV=fisco make api, then make web
make fisco-stop
```

What the port needed:
- **Time.** FISCO BCOS gives `block.timestamp` in milliseconds. The vault reads its clock through `_now()`, which converts a millisecond value to seconds; there are tests for this.
- **Funding.** The Web3 RPC refuses transactions from accounts with no balance, so the script gives fresh test keys a balance with the official console.
- **RPC details.** Log queries must name `toBlock`, and the node's batch limit is raised in its config.
- **Gas.** FISCO has no P-256 precompile, so the passkey check runs OpenZeppelin's Solidity verifier, which costs about 340,000 gas.

## Honest limits

A passkey prompt cannot show what is being signed (the dashboard shows it first, but a fully compromised computer could lie); until the student registers a passkey and locks the owner key, that key sits on the same machine; remittance quotes are sample data; scam rules are English-language rules. The full list is in the [security self-assessment](docs/SECURITY_SELF_ASSESSMENT.md#4-known-risk-list).

## License

MIT, see [LICENSE](LICENSE). Third-party components and their licences: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
