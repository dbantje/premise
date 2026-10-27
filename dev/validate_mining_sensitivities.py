"""Engineering validation only; synthetic calibration is not a research scenario."""

import argparse
import copy
import hashlib
import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="ecoinvent-3.10.1-cutoff")
    parser.add_argument("--database", default="ecoinvent-3.10.1-cutoff")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import bw2data as bd
    from wurst import extract_brightway2_databases
    from wurst.linking import link_internal, check_internal_linking
    from premise.mining_sensitivity import apply_mining_sensitivity

    if args.project not in [p.name for p in bd.projects]:
        parser.error("Project does not exist")
    bd.projects.set_current(args.project)
    if args.database not in bd.databases:
        parser.error("Database does not exist")
    data = extract_brightway2_databases([args.database])
    before = hashlib.sha256(pickle.dumps(data, protocol=5)).hexdigest()
    template = json.loads(
        (ROOT / "examples/mining_sensitivities.template.json").read_text()
    )
    output = {
        "purpose": "Software and inventory-linkage validation ONLY, not calibrated LCIA or a technological forecast",
        "project": bd.projects.current,
        "database": args.database,
        "inventory_activities": len(data),
        "cases": [],
    }
    for config in template["cases"]:
        config = copy.deepcopy(config)
        config["label"] = "QA_ONLY_" + config["label"]
        for target in config["targets"]:
            for entry in target.get("electricity", []):
                entry.update(
                    stage_share=0.6,
                    basis="SYNTHETIC QA ONLY: exercise a partial stage allocation; not a measured share",
                )
            for entry in target.get("diesel_services", []):
                entry.update(
                    haulage_share=0.75,
                    basis="SYNTHETIC QA ONLY: exercise partial service allocation",
                )
            if "combustion_service_basis" in target:
                target["combustion_service_basis"] = (
                    "QA test of the explicit diesel combustion-service path; no study calibration"
                )
            for entry in target.get("emissions", []):
                entry.update(
                    collected_fraction=0.8,
                    basis="SYNTHETIC QA ONLY: verify partial short-term collection",
                )
            if "baseline_recovery" in target:
                target.update(
                    baseline_recovery=0.9,
                    baseline_recovery_basis="SYNTHETIC QA ONLY",
                    downstream_recovery=0.8,
                    downstream_recovery_basis="SYNTHETIC QA ONLY",
                    transfer_basis="SYNTHETIC QA ONLY: exercise published Dexing factors on the ecoinvent graph",
                )
        result = apply_mining_sensitivity(data, **config)
        link_internal(result.database)
        check_internal_linking(result.database)
        # Validate semantic destinations, not just that the old input key exists.
        identities = {(ds["database"], ds["code"]): ds for ds in result.database}
        links = 0
        for ds in result.database:
            for exc in ds["exchanges"]:
                if exc["type"] == "technosphere":
                    supplier = identities[exc["input"]]
                    assert (
                        exc["name"],
                        exc["product"],
                        exc["location"],
                        exc["unit"],
                    ) == (
                        supplier["name"],
                        supplier["reference product"],
                        supplier["location"],
                        supplier["unit"],
                    )
                    links += 1
        output["cases"].append(
            {
                "case": config["case"],
                "label": config["label"],
                "changed_exchanges": result.report["changed_exchange_count"],
                "created_activities": len(result.report["created_activities"]),
                "validated_technosphere_links": links,
                "limitations": result.report["limitations"],
            }
        )
        print(json.dumps(output["cases"][-1]), flush=True)
        del result
    after = hashlib.sha256(pickle.dumps(data, protocol=5)).hexdigest()
    assert before == after, "Input inventory was modified"
    output["baseline_unchanged"] = True
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print("All seven software configurations passed; source inventory unchanged.")


if __name__ == "__main__":
    main()
