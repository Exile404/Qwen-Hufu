# Third-party notices

Hufu's own code is released under the [MIT License](LICENSE). It is built on the open-source components below. Each is used under its own licence, all of which permit this use. Versions are the ones pinned in this repository (`contracts/` install step, `agent/uv.lock`, `web/pnpm-lock.yaml`).

## Smart contracts (`contracts/`)

| Component | Version | Licence | Use |
|---|---|---|---|
| [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) | 5.4.0 | MIT | `SafeERC20`, `ReentrancyGuard`, `ERC20` (test tokens) |
| [forge-std](https://github.com/foundry-rs/forge-std) | 1.17.0 | MIT or Apache-2.0 | Test and deploy-script helpers |
| [Foundry](https://github.com/foundry-rs/foundry) (forge, cast, anvil) | 1.8.5 | MIT or Apache-2.0 | Build, test and local-chain tooling (not distributed) |

## Agent (`agent/`)

| Component | Version | Licence |
|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | 0.143.3 | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | 0.54.0 | BSD-3-Clause |
| [web3.py](https://github.com/ethereum/web3.py), eth-account, eth-abi, eth-utils | 8.0.0, 0.14.0, 6.0.0, 6.0.0 | MIT |
| [OpenAI Python SDK](https://github.com/openai/openai-python) (client for any OpenAI-compatible endpoint, here local Ollama) | 3.33.0 | Apache-2.0 |
| [Pydantic](https://github.com/pydantic/pydantic), pydantic-settings | 2.14.1, 2.16.0 | MIT |
| [HTTPX](https://github.com/encode/httpx) | 0.28.1 | BSD-3-Clause |
| [PyYAML](https://github.com/yaml/pyyaml) | 6.0.3 | MIT |
| [Rich](https://github.com/Textualize/rich) | 15.0.0 | MIT |
| Development only: pytest, pytest-cov, Ruff, mypy | 9.1.1, 7.1.0, 0.17.1, 2.4.0 | MIT |

## Dashboard (`web/`)

| Component | Version | Licence |
|---|---|---|
| [Next.js](https://github.com/vercel/next.js) | 16.4.2 | MIT |
| [React](https://github.com/facebook/react), react-dom | 19.3.0 | MIT |
| [Base UI](https://github.com/mui/base-ui) | 1.9.0 | MIT |
| [shadcn/ui](https://github.com/shadcn-ui/ui) (component scaffolding; development only) | 4.22.0 | MIT |
| [Lucide](https://github.com/lucide-icons/lucide) icons (lucide-react) | 1.58.0 | ISC |
| [class-variance-authority](https://github.com/joe-bell/cva) | 0.7.1 | Apache-2.0 |
| cn | 0.4.0 | MIT |
| [Tailwind CSS](https://github.com/tailwindlabs/tailwindcss), tw-animate-css | 4.3.3, 1.4.0 | MIT |
| [Geist](https://github.com/vercel/geist-font) fonts (loaded through `next/font`) | — | SIL Open Font License 1.1 |
| Development only: TypeScript (Apache-2.0), ESLint and eslint-config-next (MIT) | 5.9.3, 9.39.5, 16.4.2 | as listed |

## Model and runtime

| Component | Licence | Use |
|---|---|---|
| [Qwen3.5 9B](https://ollama.com/library/qwen3.5) (Alibaba Qwen team) | Apache-2.0 | The local language model, run unmodified through Ollama (`make model` builds `hufu-qwen`, which only sets the context length and temperature) |
| [Ollama](https://github.com/ollama/ollama) | MIT | Local model runtime (not distributed) |

## Data

| Data | Source | Terms |
|---|---|---|
| `agent/data/compliance/ofac_eth.txt` | U.S. Treasury OFAC SDN list ([sanctionslist.ofac.treas.gov](https://sanctionslist.ofac.treas.gov/Home/static/sdn.html)), fetched 19 Oct 2026 | Work of the U.S. Government, public domain in the U.S. Only the Ethereum-format addresses are kept |
| `agent/data/seed/` (emails, remittance routes and quotes) | Written for this project | Fictional people and sample numbers; MIT with the rest of the code |

Regulatory texts cited in [docs/COMPLIANCE.md](docs/COMPLIANCE.md) are linked, not copied.
