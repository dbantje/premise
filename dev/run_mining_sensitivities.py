"""Run mining sensitivity LCIA, audit an inventory, or export configured cases.

Quick start: python dev/run_mining_sensitivities.py --lcia
LCIA uses in-memory matrices and does not register or overwrite databases.
"""

import argparse
import json
import math
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run_lcia(args, bd, database):
    from premise.mining_sensitivity import apply_mining_sensitivity, _dataset, _identity
    from premise.mining_sensitivity_lcia import (
        MiningSensitivityLCA,
        full_coverage_cases,
        select_methods,
        score_rows,
        write_score_outputs,
    )

    if any(
        (args.output / name).exists()
        for name in ("scores.csv", "run.json", "summary.html")
    ):
        raise ValueError(
            "LCIA output already exists; select a fresh --output directory"
        )
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if (
            set(config) != {"cases"}
            or not isinstance(config["cases"], list)
            or not config["cases"]
        ):
            raise ValueError("Config must contain a nonempty cases list")
        cases, statuses = config["cases"], []
        purpose = "User-configured sensitivity cases; inspect each case's evidence and process boundaries."
    else:
        cases, statuses = full_coverage_cases(
            database,
            location=args.mine_location,
            baseline_recovery=args.baseline_recovery,
            downstream_recovery=args.downstream_recovery,
            recovery_basis=args.recovery_basis,
        )
        purpose = (
            "Full-coverage technical-potential bounds, not calibrated forecasts: published performance "
            "is applied to all modeled electricity/service inputs or short-term captured releases. "
            "Drainage/recovery potential cases omit treatment/recovery burdens unless supplied explicitly."
        )
    labels = [case.get("label", case["case"]) for case in cases]
    if len(set(labels)) != len(labels) or "baseline" in labels:
        raise ValueError("Case labels must be unique and cannot be baseline")
    for label in labels:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", label):
            raise ValueError("Invalid case label")
        if (args.output / f"{label}.json").exists():
            raise ValueError(
                f"Case report already exists: {label}; use a new output directory"
            )
        if (
            args.write_brightway
            and f"{args.database} - mining - {label}" in bd.databases
        ):
            raise ValueError(
                f"Destination Brightway database already exists for {label}"
            )
    methods = select_methods(bd.methods, args.method_family)
    if args.method_family == "ReCiPe 2016 v1.03, midpoint (H)" and len(methods) != 18:
        raise ValueError(
            f"Expected all 18 ReCiPe midpoint (H) categories; found {len(methods)} installed methods"
        )
    fu_ds = _dataset(
        database,
        {
            "name": args.fu_name,
            "reference product": args.fu_product,
            "location": args.fu_location or args.mine_location,
        },
    )
    if not math.isfinite(args.amount) or args.amount <= 0:
        raise ValueError("Functional-unit --amount must be positive and finite")
    functional_unit = {
        **_identity(fu_ds),
        "amount": args.amount,
        "unit": fu_ds["unit"],
        "key": [fu_ds["database"], fu_ds["code"]],
    }
    node = bd.get_node(database=fu_ds["database"], code=fu_ds["code"])
    # Reject invalid/uncalibrated configurations before any score is published.
    for case in cases:
        result = apply_mining_sensitivity(database, **case)
        del result
    print(purpose, flush=True)
    print(
        f"Functional unit: {args.amount:g} {fu_ds['unit']} of {fu_ds['reference product']}, {functional_unit['location']}",
        flush=True,
    )
    print(
        f"Calculating baseline and {len(cases)} variants across {len(methods)} methods...",
        flush=True,
    )
    session = MiningSensitivityLCA(database, {node.id: args.amount}, methods)
    rows = score_rows(
        "baseline",
        "baseline",
        [m["baseline"] for m in session.methods],
        session.methods,
        functional_unit,
        purpose,
    )
    metadata = {
        "project": args.project,
        "source_database": args.database,
        "source_database_metadata": dict(bd.databases[args.database]),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": purpose,
        "functional_unit": functional_unit,
        "methods": session.methods,
        "configurations": cases,
        "limitations": {},
        "numerical_residuals": {},
        "calculation": "Baseline Brightway LCI; changed and new columns scored using independent in-memory sparse matrices",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for index, case in enumerate(cases, 1):
        label = case.get("label", case["case"])
        print(f"[{index}/{len(cases)}] {label}", flush=True)
        result = apply_mining_sensitivity(database, **case)
        values = session.score(result)
        result.report["purpose"] = purpose
        result.report["baseline"] = {"project": args.project, "database": args.database}
        result.write_report(args.output / f"{label}.json")
        rows.extend(
            score_rows(
                label,
                case["case"],
                values,
                session.methods,
                functional_unit,
                purpose,
                result.report["limitations"],
            )
        )
        metadata["limitations"][label] = result.report["limitations"]
        metadata["numerical_residuals"][label] = session.last_residual
        statuses.append(
            {
                "case": case["case"],
                "label": label,
                "status": "calculated",
                "reason": " | ".join(result.report["limitations"]),
            }
        )
        if args.save_inventories:
            with (args.output / f"{label}.pickle").open("wb") as handle:
                pickle.dump(result.database, handle, protocol=pickle.HIGHEST_PROTOCOL)
        if args.write_brightway:
            from premise.brightway25 import write_brightway_database

            name = f"{args.database} - mining - {label}"
            if name in bd.databases:
                raise ValueError(f"Refusing to overwrite {name}")
            write_brightway_database(result.database, name)
        del result
        metadata["status"] = "running"
        write_score_outputs(args.output, rows, statuses, metadata)
    metadata["status"] = "complete"
    write_score_outputs(args.output, rows, statuses, metadata)
    print(f"LCIA scores: {args.output / 'scores.csv'}", flush=True)
    print(f"Comparison: {args.output / 'summary.html'}", flush=True)
    for status in statuses:
        if status["status"] != "calculated":
            print(f"{status['label']}: {status['reason']}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="ecoinvent-3.10.1-cutoff")
    parser.add_argument("--database", default="ecoinvent-3.10.1-cutoff")
    parser.add_argument(
        "--output",
        type=Path,
        help="Default: a new timestamped directory under results/",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Calibrated cases JSON; with --lcia, omit for full-coverage bounds; otherwise omit for audit only",
    )
    parser.add_argument(
        "--write-brightway",
        action="store_true",
        help="Write new, uniquely named databases; never overwrite",
    )
    parser.add_argument(
        "--lcia",
        action="store_true",
        help="Score baseline and cases; without --config use explicit full-coverage bounds",
    )
    parser.add_argument(
        "--mine-location", default="CL", help="Mining location for automatic bounds"
    )
    parser.add_argument(
        "--fu-name", default="copper mine operation and beneficiation, sulfide ore"
    )
    parser.add_argument("--fu-product", default="copper concentrate, sulfide ore")
    parser.add_argument("--fu-location", help="Default: --mine-location")
    parser.add_argument(
        "--amount",
        type=float,
        default=1.0,
        help="Functional-unit amount; default 1 kg concentrate",
    )
    parser.add_argument("--method-family", default="ReCiPe 2016 v1.03, midpoint (H)")
    parser.add_argument(
        "--baseline-recovery",
        type=float,
        help="Sourced baseline Cu recovery, fraction 0..1",
    )
    parser.add_argument(
        "--downstream-recovery",
        type=float,
        help="Sourced finishing recovery, fraction 0..1",
    )
    parser.add_argument(
        "--recovery-basis", help="Source/documentation for both recovery values"
    )
    parser.add_argument(
        "--save-inventories",
        action="store_true",
        help="Also save licensed inventory pickles during LCIA; otherwise scores/reports only",
    )
    args = parser.parse_args()
    if args.output is None:
        args.output = (
            Path("results")
            / f"mining-sensitivity-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        )
    if args.write_brightway and not args.config and not args.lcia:
        parser.error("--write-brightway requires --config")
    if not args.lcia and any(
        (
            args.baseline_recovery is not None,
            args.downstream_recovery is not None,
            args.recovery_basis,
            args.save_inventories,
        )
    ):
        parser.error("Recovery arguments and --save-inventories require --lcia")

    import bw2data as bd

    if args.project not in [p.name for p in bd.projects]:
        parser.error(f"Project does not exist: {args.project}")
    bd.projects.set_current(args.project)
    if args.database not in bd.databases:
        parser.error(f"Database does not exist: {args.database}")
    from wurst import extract_brightway2_databases
    from premise.mining_sensitivity import (
        apply_mining_sensitivity,
        inspect_mining_inventory,
        literature_sources,
    )

    # Extract exactly the requested database. External technosphere providers are
    # intentionally not guessed; the engine requires suppliers in this inventory.
    database = extract_brightway2_databases([args.database])
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "inventory-audit.json").write_text(
        json.dumps(
            {
                "project": args.project,
                "database": args.database,
                "activities": inspect_mining_inventory(database),
            },
            indent=2,
        )
        + "\n"
    )
    (args.output / "literature-sources.json").write_text(
        json.dumps(literature_sources(), indent=2) + "\n"
    )
    if args.lcia:
        run_lcia(args, bd, database)
        return
    if not args.config:
        print(f"Inventory audit written to {args.output}")
        return
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if (
        set(config) != {"cases"}
        or not isinstance(config["cases"], list)
        or not config["cases"]
    ):
        parser.error(
            "Config requires exactly one key, cases, containing a nonempty list"
        )
    labels = [case.get("label", case["case"]) for case in config["cases"]]
    if len(set(labels)) != len(labels) or not all(
        re.fullmatch(r"[A-Za-z0-9_.-]+", label) for label in labels
    ):
        parser.error(
            "Case labels must be unique and contain only letters, digits, underscores, dots or hyphens"
        )
    names = [f"{args.database} - mining - {label}" for label in labels]
    if args.write_brightway and any(name in bd.databases for name in names):
        parser.error("A destination Brightway database already exists; use new labels")
    outputs = [
        args.output / f"{label}{suffix}"
        for label in labels
        for suffix in (".json", ".pickle")
    ]
    if any(path.exists() for path in outputs):
        parser.error(
            "A case output already exists; use a new output directory or new labels"
        )
    # Validate all cases first, discarding copies to avoid holding five full LCI
    # databases in RAM. No variant is published if any configuration is invalid.
    for case in config["cases"]:
        result = apply_mining_sensitivity(database, **case)
        del result
    manifest = {"project": args.project, "source_database": args.database, "cases": []}
    for case, label, name in zip(config["cases"], labels, names):
        result = apply_mining_sensitivity(database, **case)
        result.report["baseline"] = {"project": args.project, "database": args.database}
        result.write_report(args.output / f"{label}.json")
        with (args.output / f"{label}.pickle").open("wb") as handle:
            pickle.dump(result.database, handle, protocol=pickle.HIGHEST_PROTOCOL)
        if args.write_brightway:
            from premise.brightway25 import write_brightway_database

            # The library writer can overwrite: guard immediately before each call.
            if name in bd.databases:
                raise ValueError(f"Refusing to overwrite {name}")
            write_brightway_database(result.database, name)
        manifest["cases"].append(
            {
                "case": case["case"],
                "label": label,
                "changed_exchanges": result.report["changed_exchange_count"],
                "limitations": result.report["limitations"],
                "brightway_database": name if args.write_brightway else None,
            }
        )
        del result
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(labels)} independent sensitivity cases to {args.output}")


if __name__ == "__main__":
    main()
