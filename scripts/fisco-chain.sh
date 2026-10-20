#!/usr/bin/env bash
# A one-node FISCO BCOS chain (WeBank's open consortium chain) for Hufu, with its Ethereum-compatible
# Web3 RPC on 127.0.0.1:8548. Downloads pinned, checksum-verified releases into .fisco/ (about 300 MB,
# mostly the official console, which is used once to give the test keys a balance: FISCO's Web3 RPC
# refuses transactions from accounts with none). Needs Java 8+ for the console.
#
#   make fisco-chain     # start (builds the chain on the first run)
#   make deploy-fisco    # deploy + seed the contracts (the same Foundry script as everywhere else)
#   HUFU_ENV=fisco make evidence
#   make fisco-stop
set -euo pipefail

FISCO_VERSION=v3.16.4
CONSOLE_VERSION=v3.8.0
WEB3_PORT=8548
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DIR=$ROOT/.fisco
NODE=$DIR/nodes/127.0.0.1
export PATH=$HOME/.foundry/bin:$PATH

fetch() { # url file sha256
  if [ ! -f "$DIR/$2" ]; then
    echo "Downloading $2"
    curl -fsSL -o "$DIR/$2.part" "$1"
    mv "$DIR/$2.part" "$DIR/$2"
  fi
  echo "$3  $DIR/$2" | sha256sum -c --quiet || { echo "Checksum mismatch for $2"; rm -f "$DIR/$2"; exit 1; }
}

web3() { # method
  curl -s -m 5 -X POST -H 'Content-Type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"method\":\"$1\",\"params\":[],\"id\":1}" "http://127.0.0.1:$WEB3_PORT"
}

mkdir -p "$DIR"
if [ ! -d "$NODE/node0" ]; then
  fetch "https://raw.githubusercontent.com/FISCO-BCOS/FISCO-BCOS/$FISCO_VERSION/tools/BcosAirBuilder/build_chain.sh" \
    build_chain.sh 7656ca5c72c45accd0d2754ed2ecee96f46d03b4d21da979e9c38b40e16cc4b9
  fetch "https://github.com/FISCO-BCOS/FISCO-BCOS/releases/download/$FISCO_VERSION/fisco-bcos-linux-x86_64.tar.gz" \
    fisco-bcos-linux-x86_64.tar.gz 7015851c8e7eab3b05a882cfdd71d18567f69bb2b7b79f68205c0b77d8b9d18c
  tar -xzf "$DIR/fisco-bcos-linux-x86_64.tar.gz" -C "$DIR"
  (cd "$DIR" && bash build_chain.sh -l 127.0.0.1:1 -p 30300,20200 -e ./fisco-bcos -o nodes >/dev/null)
  # Turn on the Web3 RPC: local only, on its own port, with room for Hufu's batched reads and log queries.
  python3 - "$NODE/node0/config.ini" "$WEB3_PORT" <<'EOF'
import pathlib, sys
p, port = pathlib.Path(sys.argv[1]), sys.argv[2]
s = p.read_text()
start = s.index("[web3_rpc]")
end = s.index("\n[", start + 1)
block = s[start:end].replace("enable=false", "enable=true").replace("listen_ip=0.0.0.0", "listen_ip=127.0.0.1")
block = block.replace("listen_port=8545", f"listen_port={port}")
block = block.replace("    thread_count=8", "    thread_count=8\n    batch_request_size_limit=64\n    filter_max_process_block=100000")
p.write_text(s[:start] + block + s[end:])
EOF
  NEW_CHAIN=1
fi

if ! web3 eth_chainId | grep -q result; then
  (cd "$NODE" && bash start_all.sh >/dev/null)
  for _ in $(seq 30); do web3 eth_chainId | grep -q result && break; sleep 1; done
fi
web3 eth_chainId | grep -q result || { echo "FISCO BCOS node did not start; see $NODE/node0/log"; exit 1; }

# Fresh test keys (never the public anvil keys), like `make testnet-keys`.
if [ ! -f "$ROOT/.env.fisco" ]; then
  key() { cast wallet new --json | jq -r '(.data // .) | if type == "array" then .[0] else . end | .private_key'; }
  printf '%s\n' "# FISCO BCOS (local one-node chain, Web3 RPC). Test keys only. Never commit." \
    "RPC_URL=http://127.0.0.1:$WEB3_PORT" "CHAIN_ID=20200" "EXPLORER_TX_URL=" \
    "OWNER_PRIVATE_KEY=$(key)" "AGENT_PRIVATE_KEY=$(key)" "GUARDIAN_PRIVATE_KEY=$(key)" "GAS_TOPUP_WEI=0" \
    >"$ROOT/.env.fisco"
  chmod 600 "$ROOT/.env.fisco"
  NEW_KEYS=1
fi

if [ "${NEW_CHAIN:-}${NEW_KEYS:-}" ]; then
  fetch "https://github.com/FISCO-BCOS/console/releases/download/$CONSOLE_VERSION/console.tar.gz" \
    console.tar.gz 255f1b67b22f0672612e41f93e816c2ba53a9dd3684ccc2a64c75e1b02fb6d1a
  if [ ! -d "$DIR/console" ]; then
    tar -xzf "$DIR/console.tar.gz" -C "$DIR"
  fi
  CONSOLE=$DIR/console
  cp "$NODE"/sdk/* "$CONSOLE/conf/"
  sed 's/^peers=.*/peers=["127.0.0.1:20200"]/' "$CONSOLE/conf/config-example.toml" >"$CONSOLE/conf/config.toml"
  rm -rf "$CONSOLE/account" && mkdir -p "$CONSOLE/account/ecdsa"
  cp "$DIR"/nodes/ca/accounts/* "$CONSOLE/account/ecdsa/" # the chain's admin account
  run() { (cd "$CONSOLE" && bash console.sh "$@" 2>/dev/null | grep -E "Success|success|error|Error" | head -1); }
  run setSystemConfigByKey feature_balance 1
  run setSystemConfigByKey feature_balance_precompiled 1
  set -a && . "$ROOT/.env.fisco" && set +a
  for k in OWNER AGENT GUARDIAN; do
    v=${k}_PRIVATE_KEY
    run addBalance "$(cast wallet address --private-key "${!v}")" 1000 ether
  done
fi

echo "FISCO BCOS $FISCO_VERSION running: Web3 RPC http://127.0.0.1:$WEB3_PORT (chain id 20200). Next: make deploy-fisco"
