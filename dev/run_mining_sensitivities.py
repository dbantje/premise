"""Audit a Brightway inventory or apply explicitly configured sensitivities.

The default writes reports and independent Wurst pickles, not Brightway data.
Run from the repository root: python dev/run_mining_sensitivities.py --help
"""

import argparse
import json
import pickle
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, help="JSON containing a cases list; omit for audit only"
    )
    parser.add_argument(
        "--write-brightway",
        action="store_true",
        help="Write new, uniquely named databases; never overwrite",
    )
    args = parser.parse_args()
    if args.write_brightway and not args.config:
        parser.error("--write-brightway requires --config")

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
