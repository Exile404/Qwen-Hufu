# Security policy

Hufu is a competition prototype. It runs on a local chain and on Ethereum Sepolia with **test tokens only**; it has not been audited and must not hold real funds.

## Reporting a vulnerability

Please report security issues privately through GitHub's **Report a vulnerability** button (Security tab of this repository) rather than in a public issue. Include the steps to reproduce and, if you can, the transaction hash or test that shows the problem. We aim to reply within 3 days.

In scope: the `HufuVault` contract, the guard (`agent/hufu/guard/`), the agent's tools and approval flow, and the dashboard API. Known and accepted risks are listed in the [security self-assessment](docs/SECURITY_SELF_ASSESSMENT.md#4-known-risk-list); reports that bypass one of the documented controls are especially welcome.

## How the project is checked

- `forge test` (70 tests, including fuzzing), `forge lint`, and Slither static analysis (no high or medium findings) for the contract.
- `ruff`, `mypy` and `pytest` (unit tests and on-chain integration tests on a private chain) for the agent.
- A red-team suite of 40 attacks and 15 normal tasks, judged on-chain (`make redteam`).
- Continuous integration runs the contract, agent and dashboard checks on every push (`.github/workflows/ci.yml`).
