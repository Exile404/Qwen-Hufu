"""Runtime settings, read from the repo-level .env file (plus .env.<HUFU_ENV> if set)."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


# HUFU_ENV=testnet loads .env.testnet on top of .env (its values win).
ENV_FILES = (REPO_ROOT / ".env",) + (
    (REPO_ROOT / f".env.{os.environ['HUFU_ENV']}",) if os.environ.get("HUFU_ENV") else ()
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    # LLM: any OpenAI-compatible endpoint. Default is the local Ollama model.
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "hufu-qwen"
    llm_reasoning_effort: str = "low"
    llm_max_tokens: int = 1500  # per call, thinking included; stops runaway generations

    # Chain
    rpc_url: str = "http://127.0.0.1:8545"
    chain_id: int = 31337
    explorer_tx_url: str = ""  # e.g. https://sepolia.etherscan.io/tx/
    agent_private_key: str = ""  # session key: limited by the vault policy
    owner_private_key: str = ""  # stands in for the student's phone/passkey in the demo
    guardian_private_key: str = ""  # trusted contact: can only pause

    # The account holder, for travel-rule-ready transfer records (compliance.py)
    account_holder: str = "Ayaan (demo persona)"

    # Autopilot: the agent acting on its own between conversations (see autopilot.py)
    autopilot: bool = False  # starting state; the dashboard can switch it on and off
    autopilot_interval_s: int = 20

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    web_origin: str = "http://localhost:3000"
    db_path: Path = REPO_ROOT / "agent" / "data" / "hufu.db"

    @property
    def deployments_file(self) -> Path:
        return REPO_ROOT / "contracts" / "deployments" / f"{self.chain_id}.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
