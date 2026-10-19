"""Fetch the U.S. Treasury OFAC SDN list and keep its EVM (0x…) digital-currency addresses for screening.

    make sanctions-update

OFAC publishes digital-currency addresses in the remarks of SDN.CSV ("Digital Currency Address - ETH
0x…"), continued in SDN_COMMENTS.CSV when a record is long. Every 0x address labelled this way (ETH,
USDT, USDC and other EVM chains) is kept. The download URLs can be overridden with OFAC_SDN_URL and
OFAC_SDN_COMMENTS_URL if OFAC moves them; the list page is
https://sanctionslist.ofac.treas.gov/Home/static/sdn.html
"""

import os
import re
import sys
from datetime import UTC, datetime

import httpx
from eth_utils import to_checksum_address

from hufu.compliance import OFAC_FILE

URLS = (
    os.environ.get("OFAC_SDN_URL", "https://www.treasury.gov/ofac/downloads/sdn.csv"),
    os.environ.get("OFAC_SDN_COMMENTS_URL", "https://www.treasury.gov/ofac/downloads/sdn_comments.csv"),
)
EVM_ADDRESS = re.compile(r"Digital Currency Address - [A-Za-z0-9]+[\s;:]*(0x[0-9a-fA-F]{40})(?![0-9a-fA-F])")


def parse(text: str) -> set[str]:
    return {to_checksum_address(m.group(1)) for m in EVM_ADDRESS.finditer(text)}


def main() -> int:
    found: set[str] = set()
    for url in URLS:
        try:
            r = httpx.get(url, follow_redirects=True, timeout=120)
            r.raise_for_status()
        except httpx.HTTPError as e:
            print(f"Could not download {url}: {e}", file=sys.stderr)
            return 1
        found |= parse(r.text)
        print(f"{url}: {len(r.content) / 1e6:.1f} MB, {len(found)} EVM addresses so far")
    if not found:
        print("No addresses found: the file format may have changed. Nothing written.", file=sys.stderr)
        return 1
    OFAC_FILE.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "# Ethereum-format (0x) digital-currency addresses on the OFAC SDN list.",
        f"# source: {', '.join(URLS)}",
        f"# fetched: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"# count: {len(found)}",
    ]
    OFAC_FILE.write_text("\n".join(header + sorted(found)) + "\n")
    print(f"Wrote {len(found)} addresses to {OFAC_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
