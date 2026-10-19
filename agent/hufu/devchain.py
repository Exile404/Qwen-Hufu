"""A private, throwaway local chain with a fresh deployment. Used by the tests and the red team,
so neither ever touches the demo chain on :8545."""

import os
import random
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from web3 import Web3

from hufu.config import REPO_ROOT, Settings, get_settings

FOUNDRY_BIN = Path.home() / ".foundry" / "bin"


def _bin(name: str) -> str:
    return shutil.which(name) or str(FOUNDRY_BIN / name)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def devchain(chain_id: int | None = None) -> Iterator[Settings]:
    """Start anvil on a free port with a random local chain id, deploy + seed, yield Settings."""
    chain_id = chain_id or random.randint(31400, 31999)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    anvil = subprocess.Popen(
        [_bin("anvil"), "--port", str(port), "--chain-id", str(chain_id), "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        w3 = Web3(Web3.HTTPProvider(url))
        for _ in range(50):
            if w3.is_connected():
                break
            time.sleep(0.1)
        base = get_settings()
        env = os.environ | {
            "OWNER_PRIVATE_KEY": base.owner_private_key,
            "AGENT_PRIVATE_KEY": base.agent_private_key,
            "GUARDIAN_PRIVATE_KEY": base.guardian_private_key,
        }
        subprocess.run(
            [_bin("forge"), "script", "script/Deploy.s.sol:Deploy", "--rpc-url", url, "--broadcast", "-q"],
            cwd=REPO_ROOT / "contracts",
            env=env,
            check=True,
            capture_output=True,
        )
        yield Settings(rpc_url=url, chain_id=chain_id)
    finally:
        anvil.terminate()
        (REPO_ROOT / "contracts" / "deployments" / f"{chain_id}.json").unlink(missing_ok=True)
        shutil.rmtree(
            REPO_ROOT / "contracts" / "broadcast" / "Deploy.s.sol" / str(chain_id), ignore_errors=True
        )
