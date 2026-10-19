"""Integration fixtures: a private anvil with a fresh deployment, rolled back per test."""

import pytest

from hufu.chain import Chain
from hufu.devchain import devchain
from hufu.store import Store
from hufu.tools import Context


@pytest.fixture(scope="session")
def chain_settings():
    with devchain() as settings:
        yield settings


@pytest.fixture
def ctx(chain_settings, tmp_path):
    """A Context on the test chain. Chain state is reverted after each test."""
    chain = Chain(chain_settings)
    snap = chain.w3.provider.make_request("evm_snapshot", [])["result"]
    yield Context(chain, Store(tmp_path / "test.db", chain.vault.address))
    chain.w3.provider.make_request("evm_revert", [snap])
