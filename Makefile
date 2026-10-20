SHELL := /bin/bash
export PATH := $(HOME)/.foundry/bin:$(PATH)

OZ_VERSION := v5.6.1
FORGE_STD_VERSION := v1.17.0

.PHONY: help setup model doctor chain chain-reset deploy-local show-local testnet-keys testnet-balance deploy-testnet verify-testnet testnet-topup fisco-chain fisco-stop deploy-fisco sanctions-update api web cli redteam e2e-passkey evidence test test-contracts test-agent test-integration fmt

help: ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install everything (contracts libs, python deps, web deps, model, .env)
	@test -f .env || cp .env.example .env
	@test -d contracts/lib/forge-std || (cd contracts && forge install foundry-rs/forge-std@$(FORGE_STD_VERSION) --no-git)
	@test -d contracts/lib/openzeppelin-contracts || (cd contracts && forge install OpenZeppelin/openzeppelin-contracts@$(OZ_VERSION) --no-git)
	cd agent && uv sync
	cd web && pnpm install
	$(MAKE) model

model: ## Build the local agent model (Qwen3.5 9B, 16K context) in Ollama
	ollama pull qwen3.5:9b
	ollama create hufu-qwen -f agent/Modelfile

doctor: ## Check tools, model, chain and API prerequisites
	cd agent && uv run python -m hufu.doctor

chain: ## Run the local chain (anvil). State is saved on exit and reloaded on start
	@mkdir -p .anvil
	anvil --chain-id 31337 --state .anvil/state.json

chain-reset: ## Forget the saved local chain (stop `make chain` first), then redeploy
	rm -f .anvil/state.json contracts/deployments/31337.json
	@echo "Local chain state cleared. Start 'make chain', then 'make deploy-local'."

deploy-local: ## Deploy + seed contracts on the local chain (needs `make chain`)
	cd contracts && set -a && . ../.env && set +a && \
		forge script script/Deploy.s.sol:Deploy --rpc-url $$RPC_URL --broadcast -q
	@echo "Deployed. Wrote contracts/deployments/$$(grep ^CHAIN_ID .env | cut -d= -f2).json"

show-local: ## Show the vault's live state (balances, caps, payees, mandates)
	@scripts/show-deployment.sh

testnet-keys: ## Create fresh owner/agent/guardian keys for Ethereum Sepolia in .env.testnet (never reuse anvil keys)
	@test ! -f .env.testnet || (echo ".env.testnet already exists; not overwriting." && exit 1)
	@o=$$(cast wallet new --json | jq -r '(.data // .) | if type == "array" then .[0] else . end | .private_key'); a=$$(cast wallet new --json | jq -r '(.data // .) | if type == "array" then .[0] else . end | .private_key'); \
	 g=$$(cast wallet new --json | jq -r '(.data // .) | if type == "array" then .[0] else . end | .private_key'); \
	 printf '%s\n' "# Ethereum Sepolia test keys. Test funds only. Never commit." \
	   "RPC_URL=https://ethereum-sepolia-rpc.publicnode.com" "CHAIN_ID=11155111" \
	   "EXPLORER_TX_URL=https://sepolia.etherscan.io/tx/" \
	   "OWNER_PRIVATE_KEY=$$o" "AGENT_PRIVATE_KEY=$$a" "GUARDIAN_PRIVATE_KEY=$$g" \
	   "GAS_TOPUP_WEI=2000000000000000" > .env.testnet; chmod 600 .env.testnet; \
	 case "$$o$$a$$g" in *null*|"") rm -f .env.testnet; echo "Key generation failed"; exit 1;; esac; \
	 echo "Created .env.testnet. Fund this OWNER address with ~0.01 Sepolia ETH from a faucet:"; \
	 cast wallet address --private-key $$o

testnet-balance: ## Show the Sepolia ETH balance of the three testnet keys
	@set -a && . ./.env.testnet && set +a && for k in OWNER AGENT GUARDIAN; do \
		v=$${k}_PRIVATE_KEY; addr=$$(cast wallet address --private-key $${!v}); \
		printf "%-9s %s  %s ETH\n" $$k $$addr "$$(cast balance $$addr --ether --rpc-url $$RPC_URL)"; done

# Sepolia now prices contract creation and new storage several times higher than forge's local
# simulation (Sepolia's own estimate for the token deploy is ~6.5x), so gas limits get a 10x margin.
# Only gas actually used is charged, and Sepolia gas is a few wei.
deploy-testnet: ## Deploy + seed on Ethereum Sepolia using .env.testnet
	cd contracts && set -a && . ../.env && . ../.env.testnet && set +a && \
		forge script script/Deploy.s.sol:Deploy --rpc-url $$RPC_URL --broadcast --slow -q --gas-estimate-multiplier 1000
	@echo "Deployed to Sepolia. Run the API with: HUFU_ENV=testnet make api"

verify-testnet: ## Publish the Sepolia contracts' source on Etherscan (ETHERSCAN_API_KEY in .env.testnet)
	@set -a && . ./.env.testnet && set +a && test -n "$$ETHERSCAN_API_KEY" || \
		(echo "Add ETHERSCAN_API_KEY to .env.testnet first." && exit 1)
	@set -a && . ./.env.testnet && set +a && d=$$PWD/contracts/deployments/11155111.json && cd contracts && \
	for c in vault:src/HufuVault.sol:HufuVault tHKD:src/MockStablecoin.sol:MockStablecoin \
		tUSD:src/MockStablecoin.sol:MockStablecoin fxPool:src/MockFXPool.sol:MockFXPool; do \
		addr=$$(jq -r ".$${c%%:*}" $$d); echo "== $${c%%:*} $$addr"; \
		forge verify-contract $$addr $${c#*:} --chain sepolia --verifier etherscan \
			--etherscan-api-key "$$ETHERSCAN_API_KEY" --rpc-url "$$RPC_URL" --guess-constructor-args --watch || exit 1; \
	done

testnet-topup: ## Mint test tokens into the Sepolia vault for a fresh demo (10,000 tHKD + 800 tUSD; no value)
	@set -a && . ./.env.testnet && set +a && d=contracts/deployments/11155111.json && \
	vault=$$(jq -r .vault $$d) && \
	cast send $$(jq -r .tHKD $$d) "mint(address,uint256)" $$vault 10000000000 --private-key $$OWNER_PRIVATE_KEY --rpc-url $$RPC_URL --json | jq -r '"tHKD +10,000: " + .transactionHash + " status " + .status' && \
	cast send $$(jq -r .tUSD $$d) "mint(address,uint256)" $$vault 800000000 --private-key $$OWNER_PRIVATE_KEY --rpc-url $$RPC_URL --json | jq -r '"tUSD +800:    " + .transactionHash + " status " + .status'

fisco-chain: ## Start a one-node FISCO BCOS chain (WeBank) with its Web3 RPC on :8548 (first run downloads ~300 MB into .fisco/)
	@scripts/fisco-chain.sh

fisco-stop: ## Stop the FISCO BCOS node
	@bash .fisco/nodes/127.0.0.1/stop_all.sh

deploy-fisco: ## Deploy + seed the contracts on FISCO BCOS (needs `make fisco-chain`); then HUFU_ENV=fisco make evidence / api
	cd contracts && set -a && . ../.env && . ../.env.fisco && set +a && \
		forge script script/Deploy.s.sol:Deploy --rpc-url $$RPC_URL --broadcast --legacy --with-gas-price 0 --slow -q
	@echo "Deployed to FISCO BCOS. Run the API with: HUFU_ENV=fisco make api"

sanctions-update: ## Fetch the OFAC SDN list's Ethereum addresses for counterparty screening
	cd agent && uv run python -m hufu.sanctions_update

api: ## Run the agent API on :8000 (auto-reload)
	cd agent && uv run uvicorn hufu.api:app --reload --host 127.0.0.1 --port 8000

cli: ## Chat with the agent in the terminal (needs chain, deployment, model)
	cd agent && uv run python -m hufu.cli

redteam: ## Run the red-team suite (private chain + local model; ~1 h for everything). ARGS="--configs D --only A01"
	cd agent && uv run python -m hufu.redteam $(ARGS)

e2e-passkey: ## Browser test of the passkey flow in Chrome with a virtual authenticator (no model needed)
	cd agent && uv run --with playwright==1.55.0 python ../scripts/passkey_e2e.py

evidence: ## Run the demo tasks through the guard and record every tx (HUFU_ENV=testnet for Sepolia)
	cd agent && uv run python -m hufu.evidence

web: ## Run the dashboard on :3000
	cd web && pnpm dev

test: test-contracts test-agent ## Run all offline tests

test-contracts: ## Solidity tests (Foundry)
	cd contracts && forge test -vv

test-agent: ## Python unit tests (no chain/model needed)
	cd agent && uv run pytest -q -m "not integration"

test-integration: ## Python tests that need `make chain` and the model running
	cd agent && uv run pytest -q -m integration

fmt: ## Format Solidity and Python
	cd contracts && forge fmt
	cd agent && uv run ruff format . && uv run ruff check --fix .
