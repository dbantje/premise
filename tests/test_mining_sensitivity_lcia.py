"""CLI profile and LCIA adapter tests; no proprietary data required."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from test_mining_sensitivity import inventory  # shared synthetic fixture
from premise.mining_sensitivity_lcia import (
    MiningSensitivityLCA,
    full_coverage_cases,
    score_rows,
    select_methods,
    write_score_outputs,
)


def test_full_coverage_is_explicit_and_recovery_not_imputed(inventory):
    cases, statuses = full_coverage_cases(inventory)
    assert len(cases) == 5
    assert len(statuses) == 2
    assert {s["status"] for s in statuses} == {"needs_inputs"}
    assert all(case["label"].startswith("BOUND-") for case in cases)
    assert cases[0]["targets"][0]["electricity"][0]["stage_share"] == 1
    assert "not a measured" in cases[0]["targets"][0]["electricity"][0]["basis"]
    assert cases[2]["targets"][0]["diesel_services"][0]["haulage_share"] == 1
    for case in cases[3:]:
        for flow in case["targets"][0]["emissions"]:
            assert flow["collected_fraction"] == 1
            assert "long-term" not in str(flow["exchange"]["categories"])


def test_recovery_requires_paired_values_and_evidence(inventory):
    with pytest.raises(ValueError, match="together"):
        full_coverage_cases(inventory, baseline_recovery=0.9)
    cases, statuses = full_coverage_cases(
        inventory,
        baseline_recovery=0.9,
        downstream_recovery=0.8,
        recovery_basis="Synthetic fixture has documented recovery values",
    )
    assert len(cases) == 7
    assert not statuses
    assert cases[-1]["targets"][0]["downstream_recovery_basis"].startswith("Synthetic")


def test_exact_method_family_preserves_long_term_variant():
    registry = {
        ("ReCiPe 2016 v1.03, midpoint (H)", "climate", "GWP"): {},
        ("ReCiPe 2016 v1.03, midpoint (H) no LT", "climate", "GWP"): {},
    }
    assert len(select_methods(registry)) == 1
    with pytest.raises(ValueError, match="No LCIA methods"):
        select_methods(registry, "missing")


def test_score_output_zero_baseline_and_html_escaping(tmp_path):
    methods = [{"method": ["test", "climate", "GWP"], "unit": "kg CO2", "baseline": 0}]
    fu = {
        "name": "mine <script>",
        "reference product": "concentrate",
        "location": "CL",
        "amount": 1,
        "unit": "kilogram",
    }
    rows = score_rows("baseline", "baseline", [0], methods, fu, "bounds")
    rows += score_rows(
        "BOUND-test", "hpgr", [2], methods, fu, "bounds", ["energy only"]
    )
    assert rows[-1]["percent_change"] is None
    statuses = [
        {"case": "hpgr", "label": "BOUND-test", "status": "calculated", "reason": ""}
    ]
    metadata = {
        "purpose": "bounds",
        "project": "test",
        "source_database": "source",
        "functional_unit": fu,
        "limitations": {"BOUND-test": ["energy only"]},
    }
    write_score_outputs(tmp_path, rows, statuses, metadata)
    assert (tmp_path / "scores.csv").exists()
    assert (tmp_path / "changes.csv").exists()
    assert (tmp_path / "case-status.csv").exists()
    assert "mine &lt;script&gt;" in (tmp_path / "summary.html").read_text()
    assert "mine <script>" not in (tmp_path / "summary.html").read_text()
    assert json.loads((tmp_path / "run.json").read_text())["purpose"] == "bounds"


def _roundtrip_in_isolated_project():
    """Compare every matrix-based result against a registered Brightway variant."""
    import hashlib
    import bw2data as bd
    import bw2calc as bc
    from wurst import extract_brightway2_databases
    from premise.brightway25 import write_brightway_database
    from premise.mining_sensitivity import apply_mining_sensitivity
    from test_mining_sensitivity import bio

    data = inventory.__wrapped__()
    data[0]["exchanges"].append(bio("Carbon dioxide, fossil", 0.5, ("air",)))
    bd.projects.set_current("mining-lcia-roundtrip")
    flows = {}
    by_name = {}
    for ds in data:
        for exc in ds["exchanges"]:
            if exc["type"] != "biosphere":
                continue
            identity = exc["name"], tuple(exc["categories"]), exc["unit"]
            code = hashlib.sha256(repr(identity).encode()).hexdigest()
            exc["input"] = ("bio", code)
            flows[exc["input"]] = {
                "name": exc["name"],
                "categories": exc["categories"],
                "unit": exc["unit"],
                "type": "emission",
            }
            by_name[identity] = exc["input"]
    bd.Database("bio").write(flows)
    bd.Database("test").write({(ds["database"], ds["code"]): ds for ds in data})
    methods = [("toy", "climate", "GWP"), ("toy", "toxicity", "HTOX")]
    bd.Method(methods[0]).register(unit="kg CO2 eq")
    bd.Method(methods[0]).write(
        [(by_name[("Carbon dioxide, fossil", ("air",), "kilogram")], 1)]
    )
    bd.Method(methods[1]).register(unit="toxicity unit")
    bd.Method(methods[1]).write(
        [
            (key, 100 if "long-term" in str(identity[1]) else 2)
            for identity, key in by_name.items()
            if identity[0] in ("Copper ion", "Zinc II", "Cadmium II")
        ]
    )
    original = extract_brightway2_databases(["test"])
    mine_code = data[7]["code"]
    mine = bd.get_node(database="test", code=mine_code)
    session = MiningSensitivityLCA(original, {mine.id: 1}, methods)
    cases, _ = full_coverage_cases(
        original,
        baseline_recovery=0.9,
        downstream_recovery=0.8,
        recovery_basis="Synthetic ore/recovery model",
    )
    for i, config in enumerate(cases):
        result = apply_mining_sensitivity(original, **config)
        actual = session.score(result)
        name = f"registered-variant-{i}"
        write_brightway_database(copy.deepcopy(result.database), name)
        node = bd.get_node(database=name, code=mine_code)
        check = bc.LCA({node.id: 1}, methods[0])
        check.lci()
        expected = []
        for method in methods:
            check.switch_method(method)
            check.lcia()
            expected.append(float(check.score))
        # Registered Brightway processing can quantize exchange coefficients to
        # float32; the adapter retains full-precision changed coefficients.
        np.testing.assert_allclose(actual, expected, rtol=2e-7, atol=1e-12)
    print("ROUNDTRIP_OK: 7 variants x 2 LCIA methods")


def test_matrix_scores_match_registered_brightway_in_separate_project(tmp_path):
    import csv

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["BRIGHTWAY2_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(root) + os.pathsep + str(root / "tests")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from test_mining_sensitivity_lcia import _roundtrip_in_isolated_project; _roundtrip_in_isolated_project()",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "ROUNDTRIP_OK: 7 variants x 2 LCIA methods" in completed.stdout
    # Exercise the actual one-command entry point, including recovery and a
    # non-unit demand, without touching the user's Brightway projects.
    report_dir = tmp_path / "cli-report"
    cli = subprocess.run(
        [
            sys.executable,
            "dev/run_mining_sensitivities.py",
            "--lcia",
            "--project",
            "mining-lcia-roundtrip",
            "--database",
            "test",
            "--method-family",
            "toy",
            "--amount",
            "2",
            "--baseline-recovery",
            "0.9",
            "--downstream-recovery",
            "0.8",
            "--recovery-basis",
            "Synthetic fixture recovery model",
            "--output",
            str(report_dir),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cli.returncode == 0, cli.stdout + cli.stderr
    rows = list(csv.DictReader((report_dir / "scores.csv").open()))
    assert len(rows) == 16  # baseline + all seven configurations, two methods
    climate = {
        r["scenario"]: float(r["score"]) for r in rows if r["category"] == "climate"
    }
    assert climate["baseline"] == pytest.approx(13)
    assert climate["BOUND-hpgr"] == pytest.approx(6.8, rel=2e-7)
    assert json.loads((report_dir / "run.json").read_text())["status"] == "complete"
    assert not list(report_dir.glob("*.pickle"))


def test_reconstructed_column_mismatch_is_rejected():
    from scipy import sparse

    with pytest.raises(ValueError, match="mismatch"):
        MiningSensitivityLCA._check_column(sparse.csc_matrix([[1.0]]), 0, {0: 2.0})
