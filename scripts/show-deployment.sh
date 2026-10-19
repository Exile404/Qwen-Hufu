#!/usr/bin/env bash
# Print the vault's live state: roles, balances, caps, payees, mandates, requests.
# Usage: scripts/show-deployment.sh [chainId]   (default: CHAIN_ID from .env, else 31337)
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.foundry/bin:$PATH"
# .env supplies defaults; variables already set in the environment win.
_RPC_URL="${RPC_URL:-}"; _CHAIN_ID="${CHAIN_ID:-}"
set -a; [ -f .env ] && . ./.env; set +a
RPC_URL="${_RPC_URL:-${RPC_URL:-}}"; CHAIN_ID="${_CHAIN_ID:-${CHAIN_ID:-}}"

CHAIN="${1:-${CHAIN_ID:-31337}}"
FILE="contracts/deployments/${CHAIN}.json"
RPC="${RPC_URL:-http://127.0.0.1:8545}"
[ -f "$FILE" ] || { echo "No deployment at $FILE. Run: make deploy-local"; exit 1; }

j() { jq -r "$1" "$FILE"; }
VAULT=$(j .vault); HKD=$(j .tHKD); USD=$(j .tUSD)
call() { cast call --rpc-url "$RPC" "$@"; }
fmt() { cast format-units "$(echo "$1" | awk '{print $1}')" 6; }
when() { date -u -d "@$1" "+%Y-%m-%d %H:%M UTC"; }

echo "== HufuVault $VAULT (chain $CHAIN)"
echo "owner     $(call "$VAULT" "owner()(address)")"
echo "agent     $(call "$VAULT" "agent()(address)")  expires $(when "$(call "$VAULT" "agentExpiry()(uint64)" | awk '{print $1}')")"
echo "guardian  $(call "$VAULT" "guardian()(address)")"
echo "paused    $(call "$VAULT" "paused()(bool)")   agentActive $(call "$VAULT" "agentActive()(bool)")"

echo; echo "== Balances and caps"
for pair in "tHKD:$HKD" "tUSD:$USD"; do
  sym=${pair%%:*}; tok=${pair#*:}
  bal=$(call "$tok" "balanceOf(address)(uint256)" "$VAULT")
  mapfile -t pol < <(call "$VAULT" "tokenPolicy(address)(bool,uint256,uint256)" "$tok")
  left=$(call "$VAULT" "remainingToday(address)(uint256)" "$tok")
  printf "%-5s balance %10s | per-tx cap %8s | daily cap %8s | left today %8s\n" \
    "$sym" "$(fmt "$bal")" "$(fmt "${pol[1]}")" "$(fmt "${pol[2]}")" "$(fmt "$left")"
done

echo; echo "== Payees (allowlist)"
for name in $(jq -r '.payees | keys[]' "$FILE"); do
  addr=$(j ".payees.$name")
  mapfile -t p < <(call "$VAULT" "payees(address)(bool,string)" "$addr")
  cap=$(call "$VAULT" "payeeCap(address,address)(uint256)" "$addr" "$HKD")
  capstr=$([ "$(echo "$cap" | awk '{print $1}')" = "0" ] && echo "-" || echo "$(fmt "$cap") tHKD")
  printf "%-11s %s allowed=%-5s cap=%-12s %s\n" "$name" "$addr" "${p[0]}" "$capstr" "${p[1]}"
done

echo; echo "== Mandates"
n=$(call "$VAULT" "mandateCount()(uint256)")
for ((i = 0; i < n; i++)); do
  m=$(call "$VAULT" "getMandate(uint256)((address,address,uint256,uint64,uint64,uint64,bool,string))" "$i")
  python3 - "$i" "$m" "$FILE" <<'PY'
import json, re, sys, datetime as dt
i, raw, path = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(path))
names = {v.lower(): k for k, v in d["payees"].items()}
syms = {d["tHKD"].lower(): "tHKD", d["tUSD"].lower(): "tUSD"}
label = re.search(r'"(.*)"\)$', raw).group(1)
f = [x.split(" ")[0] for x in raw.strip("()").rsplit(", \"", 1)[0].split(", ")]
payee, token, amount, interval, next_due, end, active = f
day = lambda t: "open-ended" if t == "0" else dt.datetime.fromtimestamp(int(t), dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
every = "one-off" if interval == "0" else f"every {int(interval) // 86400} days"
print(f"#{i} {label}: {int(amount) / 1e6:g} {syms.get(token.lower(), token)} to {names.get(payee.lower(), payee)}, "
      f"{every}, next due {day(next_due)}, ends {day(end)}, active={active}")
PY
done
[ "$n" = "0" ] && echo "(none)"

echo; echo "== Payment requests (T2)"
r=$(call "$VAULT" "requestCount()(uint256)")
for ((i = 0; i < r; i++)); do
  echo "#$i $(call "$VAULT" "getRequest(uint256)((address,address,uint256,bytes32,uint64,uint8))" "$i")"
done
[ "$r" = "0" ] && echo "(none)"
