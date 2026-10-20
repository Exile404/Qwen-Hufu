"""The red-team report's maths and table rendering (no chain, no model)."""

import yaml

from hufu.config import REPO_ROOT
from hufu.redteam import RunResult, summarize, wilson


def test_wilson_interval():
    assert wilson(0, 0) == (0.0, 0.0)
    lo, hi = wilson(0, 40)
    assert lo == 0.0 and 0.08 < hi < 0.10  # zero successes in 40 still leaves ~9% upper bound
    lo, hi = wilson(20, 40)
    assert 0.34 < lo < 0.36 and 0.64 < hi < 0.66


def test_scenarios_file_is_well_formed():
    spec = yaml.safe_load((REPO_ROOT / "redteam" / "scenarios.yaml").read_text())
    ids = [s["id"] for s in spec["attacks"] + spec["benign"]]
    assert len(ids) == len(set(ids))
    assert len(spec["attacks"]) == 40 and len({s["category"] for s in spec["attacks"]}) == 8
    assert all(s.get("expect") for s in spec["benign"])


def test_summary_tables():
    rs = [
        RunResult("D", "A01", "attack", "indirect_injection", "t", False, escalated_to_student=True),
        RunResult(
            "A",
            "A01",
            "attack",
            "indirect_injection",
            "t",
            True,
            breaches=["9000 HKD reached the attacker"],
            stolen_hkd=9000,
        ),
        RunResult("D", "N01", "benign", "benign", "b", True),
        RunResult("A", "N01", "benign", "benign", "b", False, failed_checks=["x"]),
    ]
    md = summarize(
        rs, {"run_id": "r", "model": "m", "reasoning": "low", "attacks": 1, "benign": 1, "minutes": 1}
    )
    assert "| **D** full Hufu (guard + vault) | 0/1 (0%)" in md
    assert "| **A** naive (no guard, plain wallet) | 1/1 (100%)" in md and "9,000" in md
    assert "| A01 t | ✗ | ⏳ |" in md  # columns in A..D order


def test_fisher_exact_matches_known_values():
    from hufu.redteam import fisher_exact

    one, two = fisher_exact(5, 40, 0, 40)  # 5/40 vs 0/40: C(40,5)/C(80,5)
    assert abs(one - 0.02737) < 1e-4 and abs(two - 0.05474) < 1e-4
    assert fisher_exact(1, 40, 0, 40) == (0.5, 1.0)
    assert fisher_exact(0, 40, 0, 40)[0] == 1.0
