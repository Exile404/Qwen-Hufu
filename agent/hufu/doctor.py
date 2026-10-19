"""Environment check: `make doctor`. Prints one line per check and exits non-zero on failure."""

import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from hufu import llm
from hufu.api import chain_status
from hufu.chain import Chain, ChainNotDeployed
from hufu.config import REPO_ROOT, get_settings

console = Console()
FOUNDRY_BIN = Path.home() / ".foundry" / "bin"


def _which(cmd: str) -> str | None:
    return shutil.which(cmd) or (str(FOUNDRY_BIN / cmd) if (FOUNDRY_BIN / cmd).exists() else None)


def main() -> int:
    failures = 0

    def report(name: str, ok: bool, detail: str = "", required: bool = True, fix: str = "") -> None:
        nonlocal failures
        if not ok and fix:
            detail = f"{detail}  -> {fix}".strip()
        mark = "[green]PASS[/]" if ok else ("[red]FAIL[/]" if required else "[yellow]WARN[/]")
        console.print(f"{mark}  {name:<28} {detail}")
        failures += (not ok) and required

    for cmd in ["forge", "anvil", "uv", "node", "pnpm", "ollama"]:
        path = _which(cmd)
        report(f"tool: {cmd}", path is not None, path or "not found")

    report(".env present", (REPO_ROOT / ".env").exists(), fix="cp .env.example .env")

    s = get_settings()
    if s.llm_base_url.startswith("http://127.0.0.1:11434"):
        models = subprocess.run(["ollama", "list"], check=False, capture_output=True, text=True).stdout
        report(f"ollama model: {s.llm_model}", s.llm_model in models, fix="make model")

    probe = llm.probe()
    report("llm tool-call probe", probe["ok"], str(probe))

    chain = chain_status()
    report("chain reachable", chain["ok"], str(chain), fix="make chain")

    deployed, detail = False, str(s.deployments_file.relative_to(REPO_ROOT))
    if s.deployments_file.exists() and chain["ok"]:
        try:
            Chain(s)
            deployed = True
        except ChainNotDeployed:
            detail += " is stale: the chain was restarted"
    report("contracts deployed", deployed, detail, fix="make deploy-local")

    console.print(
        "\n[bold green]All required checks passed.[/]"
        if not failures
        else f"\n[bold red]{failures} check(s) failed.[/]"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
