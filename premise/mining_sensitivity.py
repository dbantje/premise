"""Optional, literature-grounded perturbations of prepared Wurst inventories.

Nothing is selected or changed by default. Exact selectors and evidence for the
stage/collection shares are required because mining inventories aggregate stages.
See docs/mining-sensitivities.rst for boundaries and configuration examples.
"""

import copy
import json
import math
import numbers
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

CASES = {
    "grinding_best_practice": "doe_2007",
    "hpgr": "mcivor_2023",
    "haulage_electrification": "aydogdu_2024",
    "drainage_treatment": "hustwit_sykes_1994",
    "copper_recovery": "sun_2025",
}
METALS = {"Copper ion": "copper", "Zinc II": "zinc", "Cadmium II": "cadmium"}
UNCERTAINTY_FIELDS = (
    "loc",
    "scale",
    "shape",
    "minimum",
    "maximum",
    "negative",
    "pedigree",
    "scale without pedigree",
    "formula",
)
TARGET_FIELDS = {
    "grinding_best_practice": {"electricity"},
    "hpgr": {"electricity"},
    "haulage_electrification": {
        "electricity_supplier",
        "diesel_services",
        "combustion_service_air_emissions",
        "combustion_service_basis",
    },
    "drainage_treatment": {"performance", "scope", "emissions"},
    "copper_recovery": {
        "performance",
        "scope",
        "baseline_recovery",
        "baseline_recovery_basis",
        "downstream_recovery",
        "downstream_recovery_basis",
        "transfer_basis",
        "tailings_model",
        "tailings",
    },
}


def literature_sources():
    """Return the versioned numerical assumptions and their primary sources."""
    path = Path(__file__).parent / "data" / "mining" / "sensitivity_sources.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class SensitivityResult:
    database: list
    report: dict

    def write_report(self, path):
        Path(path).write_text(
            json.dumps(self.report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, name, lower=0, upper=None, strictly_positive=False):
    _require(
        isinstance(value, numbers.Real)
        and not isinstance(value, bool)
        and math.isfinite(value),
        f"{name} must be a finite number",
    )
    _require(
        value > lower if strictly_positive else value >= lower,
        f"{name} must be {'greater than' if strictly_positive else 'at least'} {lower}",
    )
    if upper is not None:
        _require(value <= upper, f"{name} must be at most {upper}")
    return float(value)


def _basis(value, name):
    _require(
        isinstance(value, str) and bool(value.strip()),
        f"{name}: provide a source or documented inventory basis",
    )
    return value


def _plain(value):
    """Reports must also serialize amounts from premise's float32 caches."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value)
    return value


def _keys(obj, allowed, context):
    _require(isinstance(obj, dict), f"{context} must be a dictionary")
    _require(not set(obj) - allowed, f"Unknown {context} keys: {set(obj) - allowed}")


def _identity(ds):
    return {key: ds[key] for key in ("name", "reference product", "location")}


def _matches(obj, selector):
    return all(
        (
            tuple(obj.get(key, ())) == tuple(value)
            if key == "categories"
            else obj.get(key) == value
        )
        for key, value in selector.items()
    )


def _dataset(database, selector):
    _require(isinstance(selector, dict), "Activity selector must be a dictionary")
    _require(
        set(selector) == {"name", "reference product", "location"},
        "Select activities by exact name, reference product and location",
    )
    matches = [ds for ds in database if _matches(ds, selector)]
    _require(
        len(matches) == 1, f"Expected one activity for {selector}; found {len(matches)}"
    )
    return matches[0]


def _exchange(ds, selector, kind="technosphere"):
    required = (
        {"name", "unit", "location", "product"}
        if kind == "technosphere"
        else {"name", "unit", "categories"}
    )
    _require(
        isinstance(selector, dict) and required <= set(selector),
        f"{kind} exchange selector requires {sorted(required)}",
    )
    _require(
        set(selector) <= required | {"type", "input"}, "Unknown exchange selector key"
    )
    matches = [
        e for e in ds["exchanges"] if e["type"] == kind and _matches(e, selector)
    ]
    _require(
        len(matches) == 1,
        f"Expected one {kind} exchange in {_identity(ds)} for {selector}; found {len(matches)}",
    )
    return matches[0]


def _scale(exc, factor):
    """Scale signed exchanges without retaining stale uncertainty parameters."""
    _number(factor, "exchange scaling factor")
    uncertainty = exc.get("uncertainty type", 0)
    _require(
        uncertainty in (0, 1, 2, 3, 4, 5),
        f"Unsupported uncertainty distribution {uncertainty}",
    )
    exc["amount"] *= factor
    exc.pop("formula", None)
    if factor == 0 or uncertainty in (0, 1):
        for field in UNCERTAINTY_FIELDS:
            exc.pop(field, None)
        exc["uncertainty type"] = 0
    else:
        if "loc" in exc:
            exc["loc"] = (
                exc["loc"] + math.log(factor)
                if uncertainty == 2
                else exc["loc"] * factor
            )
        if uncertainty != 2:
            for field in ("scale", "scale without pedigree"):
                if field in exc:
                    exc[field] *= factor
        for field in ("minimum", "maximum"):
            if field in exc:
                exc[field] *= factor


def _new_exchange(supplier, amount):
    return {
        "type": "technosphere",
        "name": supplier["name"],
        "product": supplier["reference product"],
        "location": supplier["location"],
        "unit": supplier["unit"],
        "amount": amount,
        "uncertainty type": 0,
    }


def _record(report, ds, exc, before, reason):
    report["changes"].append(
        {
            "activity": _identity(ds),
            "exchange": {
                k: exc[k]
                for k in ("name", "product", "categories", "location", "unit", "type")
                if k in exc
            },
            "before": before,
            "after": exc["amount"],
            "reason": reason,
        }
    )


def _change(report, ds, exc, factor, reason):
    before = exc["amount"]
    _scale(exc, factor)
    _record(report, ds, exc, before, reason)


def _additional(database, ds, target, report, multiplier=1):
    entries = target.get("additional_exchanges", [])
    if entries:
        _basis(target.get("additional_inventory_basis"), "additional_inventory_basis")
    for entry in entries:
        _require(
            set(entry) == {"supplier", "amount"},
            "Each additional exchange requires supplier and signed amount",
        )
        supplier = _dataset(database, entry["supplier"])
        amount = entry["amount"]
        _require(
            isinstance(amount, numbers.Real)
            and not isinstance(amount, bool)
            and math.isfinite(amount)
            and amount != 0,
            "Additional exchange amount must be finite and nonzero",
        )
        exc = _new_exchange(supplier, amount * multiplier)
        ds["exchanges"].append(exc)
        _record(report, ds, exc, 0, "User-sourced supplementary process inventory")


def _unit_production(ds, expected=1):
    production = [e for e in ds["exchanges"] if e["type"] == "production"]
    _require(
        len(production) == 1 and production[0]["amount"] == expected,
        "Sensitivity requires one reference production exchange with the expected amount",
    )


def _supplier(database, exc):
    ds = _dataset(
        database,
        {
            "name": exc["name"],
            "reference product": exc["product"],
            "location": exc["location"],
        },
    )
    _require(ds["unit"] == exc["unit"], "Supplier/exchange unit mismatch")
    return ds


def _clone(ds, label):
    clone = copy.deepcopy(ds)
    clone["name"] = f"{ds['name']} [mining sensitivity: {label}]"
    clone["code"] = uuid.uuid4().hex
    clone.pop("id", None)
    clone["comment"] = (
        ds.get("comment", "")
        + f"\nIndependent supplier copy for mining sensitivity {label}."
    )
    for exc in clone["exchanges"]:
        exc.pop("output", None)
        if exc["type"] == "production":
            exc["name"] = clone["name"]
            exc.pop("input", None)
    return clone


def _replace_provider(exc, supplier):
    exc.update(
        {
            "name": supplier["name"],
            "product": supplier["reference product"],
            "location": supplier["location"],
            "unit": supplier["unit"],
        }
    )
    exc.pop("input", None)
    exc.pop("activity", None)
    exc.pop("output", None)


def _energy(database, ds, target, params, report, case):
    reduction = params[
        (
            "grinding_energy_reduction"
            if case == "grinding_best_practice"
            else "grinding_machine_energy_reduction"
        )
    ]
    entries = target.get("electricity", [])
    _require(bool(entries), "Provide at least one electricity stage allocation")
    seen = set()
    for entry in entries:
        _keys(entry, {"exchange", "stage_share", "basis"}, "electricity allocation")
        exc = _exchange(ds, entry["exchange"])
        _require(
            id(exc) not in seen, "An electricity exchange is selected more than once"
        )
        seen.add(id(exc))
        _require(
            exc["unit"] == "kilowatt hour"
            and exc["amount"] > 0
            and exc["product"].startswith("electricity"),
            "Grinding changes require a positive electricity input in kWh",
        )
        _supplier(database, exc)
        share = _number(
            entry.get("stage_share"), "stage_share", upper=1, strictly_positive=True
        )
        _basis(entry.get("basis"), "Grinding stage allocation")
        _change(
            report,
            ds,
            exc,
            1 - share * reduction,
            f"{case}: {reduction:g} reduction of {share:g} stage share",
        )
    _additional(database, ds, target, report)
    if case == "hpgr":
        report["limitations"].append(
            "HPGR machine-energy sensitivity: auxiliary, machinery and media changes are only represented if supplied explicitly."
        )


def _electric_service(database, source, electricity, report, label, stack=()):
    """Clone the fuel-service path; never electrify a shared global provider."""
    key = tuple(_identity(source).values())
    _require(key not in stack, "Cycle in diesel service supply chain")
    _unit_production(source)
    _require(
        source["unit"] == "megajoule",
        "Diesel service must be in MJ of baseline fuel input",
    )
    clone = _clone(source, label)
    tech = [
        e for e in clone["exchanges"] if e["type"] == "technosphere" and e["amount"] > 0
    ]
    fuels = [
        e
        for e in tech
        if e.get("product") in ("diesel", "diesel, low-sulfur")
        and e["unit"] == "kilogram"
    ]
    if fuels:
        _require(
            len(fuels) == 1 and "burned in" in source["name"],
            "Expected a documented diesel combustion service with one raw-diesel input",
        )
        fuel = fuels[0]
        _change(
            report,
            clone,
            fuel,
            0,
            "Diesel displaced by electricity; existing machinery/lubricants retained",
        )
        for exc in clone["exchanges"]:
            if exc["type"] == "biosphere" and tuple(exc.get("categories", ()))[:1] == (
                "air",
            ):
                _change(
                    report,
                    clone,
                    exc,
                    0,
                    "Direct combustion-service air emissions removed",
                )
        params = report["source"]["parameters"]
        amount = params["diesel_efficiency"] / params["electric_efficiency"] / 3.6
        exc = _new_exchange(electricity, amount)
        clone["exchanges"].append(exc)
        _record(
            report,
            clone,
            exc,
            0,
            "Work-equivalent electricity, kWh per MJ baseline diesel",
        )
    else:
        services = [
            e
            for e in tech
            if e["unit"] == "megajoule"
            and e.get("product") == source["reference product"]
        ]
        _require(
            source["name"].startswith(("market for ", "market group for "))
            and len(services) == 1
            and services[0]["amount"] == 1,
            "Only a single-provider, unit-weight diesel service market can be unwrapped",
        )
        child = _electric_service(
            database,
            _supplier(database, services[0]),
            electricity,
            report,
            label,
            stack + (key,),
        )
        _replace_provider(services[0], child)
    database.append(clone)
    report["created_activities"].append(_identity(clone))
    return clone


def _electrify(database, ds, target, report, label):
    _require(
        target.get("combustion_service_air_emissions") is True,
        "Explicitly confirm combustion_service_air_emissions and document its basis",
    )
    _basis(target.get("combustion_service_basis"), "combustion_service_basis")
    electricity = _dataset(database, target["electricity_supplier"])
    _require(
        electricity["unit"] == "kilowatt hour"
        and electricity["reference product"].startswith("electricity"),
        "Select an electricity supplier in kWh",
    )
    entries = target.get("diesel_services", [])
    _require(bool(entries), "Select at least one MJ diesel combustion-service input")
    seen = set()
    for index, entry in enumerate(entries):
        _keys(entry, {"exchange", "haulage_share", "basis"}, "haulage allocation")
        exc = _exchange(ds, entry["exchange"])
        _require(id(exc) not in seen, "A diesel service is selected more than once")
        seen.add(id(exc))
        _require(
            exc["unit"] == "megajoule"
            and exc["amount"] > 0
            and exc["product"] == "diesel, burned in building machine",
            "Only MJ building-machine diesel services are supported; do not select raw fuel or diesel generation",
        )
        share = _number(
            entry.get("haulage_share"), "haulage_share", upper=1, strictly_positive=True
        )
        _basis(entry.get("basis"), "Haulage allocation")
        supplier = _electric_service(
            database,
            _supplier(database, exc),
            electricity,
            report,
            f"{label}-{uuid.uuid4().hex[:8]}-{index}",
        )
        amount = exc["amount"] * share
        _change(
            report,
            ds,
            exc,
            1 - share,
            "Retain non-haulage share of original diesel service",
        )
        new_exc = _new_exchange(supplier, amount)
        ds["exchanges"].append(new_exc)
        _record(report, ds, new_exc, 0, "Mine-specific electrified service")
    _additional(database, ds, target, report)
    report["limitations"].append(
        "Published conveyor efficiencies transferred to selected haulage service. Existing machinery/lubricants retained as proxies; supplementary electric-equipment inventories are not invented."
    )


def _drainage(database, ds, target, report):
    period = target.get("performance", "third")
    _require(period in ("second", "third"), "performance must be second or third")
    efficiencies = report["source"]["parameters"][period]
    scope = target.get("scope")
    _require(
        scope in ("emission_potential", "with_treatment_inventory"),
        "Specify drainage scope: emission_potential or with_treatment_inventory",
    )
    if scope == "with_treatment_inventory":
        _require(
            bool(target.get("additional_exchanges")),
            "Supply treatment inputs and residue-management inventory",
        )
    else:
        report["limitations"].append(
            "Drainage emission-potential case excludes treatment burdens; not a complete treatment LCA."
        )
    entries = target.get("emissions", [])
    _require(bool(entries), "Select drainage emissions explicitly")
    seen = set()
    for entry in entries:
        _keys(
            entry,
            {
                "exchange",
                "collected_fraction",
                "basis",
                "baseline_removal",
                "baseline_removal_basis",
            },
            "drainage emission",
        )
        exc = _exchange(ds, entry["exchange"], "biosphere")
        _require(id(exc) not in seen, "A drainage emission is selected more than once")
        seen.add(id(exc))
        categories = tuple(exc.get("categories", ()))
        _require(
            categories[:1] == ("water",)
            and exc["name"] in METALS
            and exc["unit"] == "kilogram"
            and exc["amount"] > 0,
            "Treatment performance is available only for positive aqueous Cu, Zn and Cd releases in kg",
        )
        if any(
            "long-term" in c.lower() or "long term" in c.lower() for c in categories
        ):
            raise ValueError(
                "Long-term tailings releases cannot be assigned operational drainage-removal efficiencies"
            )
        captured = _number(
            entry.get("collected_fraction"),
            "collected_fraction",
            upper=1,
            strictly_positive=True,
        )
        _basis(entry.get("basis"), "Drainage collection and treatment duration")
        baseline = _number(
            entry.get("baseline_removal", 0), "baseline_removal", upper=1
        )
        _require(baseline < 1, "baseline_removal must be less than one")
        if baseline:
            _basis(entry.get("baseline_removal_basis"), "baseline_removal_basis")
        eta = efficiencies[METALS[exc["name"]]]
        _require(
            eta >= baseline,
            "Selected treatment must not be worse than baseline_removal",
        )
        factor = 1 - captured + captured * (1 - eta) / (1 - baseline)
        _change(
            report,
            ds,
            exc,
            factor,
            f"{period} performance, removal={eta:g}, collected fraction={captured:g}, baseline removal={baseline:g}",
        )
    _additional(database, ds, target, report)


def _tailings_clone(
    database, source, report, label, mass_factor, copper_factor, stack=()
):
    key = tuple(_identity(source).values())
    _require(key not in stack, "Cycle in tailings treatment supply chain")
    _require(
        source["unit"] == "kilogram", "Tailings treatment must have kg functional unit"
    )
    _unit_production(source, expected=-1)
    clone = _clone(source, label)
    for exc in clone["exchanges"]:
        if exc["type"] == "biosphere" and tuple(exc.get("categories", ()))[:1] == (
            "water",
        ):
            factor = mass_factor * (
                copper_factor
                if exc["name"] in ("Copper ion", "Copper II", "Copper")
                else 1
            )
            _change(
                report,
                clone,
                exc,
                factor,
                "Constant leachable fraction: conserve other aqueous loads per ore input; remove recovered Cu",
            )
        elif (
            exc["type"] == "technosphere"
            and "tailing" in exc.get("product", "").lower()
        ):
            child = _tailings_clone(
                database,
                _supplier(database, exc),
                report,
                label,
                mass_factor,
                copper_factor,
                stack + (key,),
            )
            _replace_provider(exc, child)
    database.append(clone)
    report["created_activities"].append(_identity(clone))
    return clone


def _recovery(database, ds, target, report, label):
    _require(
        ds["name"] == "copper mine operation and beneficiation, sulfide ore"
        and ds["reference product"] == "copper concentrate, sulfide ore"
        and ds["unit"] == "kilogram",
        "Copper recovery requires the kg sulfide-concentrate mining/beneficiation dataset",
    )
    baseline = _number(
        target.get("baseline_recovery"),
        "baseline_recovery",
        upper=1,
        strictly_positive=True,
    )
    _require(baseline < 1, "baseline_recovery must be less than one")
    _basis(target.get("baseline_recovery_basis"), "baseline_recovery_basis")
    downstream = _number(
        target.get("downstream_recovery"),
        "downstream_recovery",
        upper=1,
        strictly_positive=True,
    )
    _basis(target.get("downstream_recovery_basis"), "downstream_recovery_basis")
    _basis(
        target.get("transfer_basis"),
        "Transfer of Dexing coarse-tailings fractions to target mine",
    )
    _require(
        target.get("tailings_model") == "constant_leachable_fraction",
        "Explicitly select tailings_model=constant_leachable_fraction",
    )
    scope = target.get("scope")
    _require(
        scope in ("yield_potential", "with_recovery_inventory"),
        "Specify recovery scope: yield_potential or with_recovery_inventory",
    )
    if scope == "with_recovery_inventory":
        _require(
            bool(target.get("additional_exchanges")),
            "Supply a recovery/finishing inventory per kg coarse tailings",
        )
    performance = target.get("performance", "low")
    _require(performance in ("low", "high"), "Recovery performance must be low or high")
    params = report["source"]["parameters"]
    capture = (
        params["coarse_fraction_share_of_copper_loss"]
        * params[f"{performance}_recovery"]
        * downstream
    )
    output_gain = (1 - baseline) / baseline * capture
    normalization = 1 / (1 + output_gain)
    tailings = _exchange(ds, target["tailings"])
    _require(
        tailings["amount"] < 0
        and tailings["unit"] == "kilogram"
        and "sulfidic tailings" in tailings["product"]
        and "non-sulfidic" not in tailings["product"],
        "Select the negative kg sulfidic-tailings waste exchange",
    )
    original_mass = -tailings["amount"]
    remaining_mass = original_mass - output_gain
    _require(
        remaining_mass > 0, "Recovered concentrate mass exceeds available tailings"
    )
    supplier = _tailings_clone(
        database,
        _supplier(database, tailings),
        report,
        f"{label}-{uuid.uuid4().hex[:8]}",
        original_mass / remaining_mass,
        1 - capture,
    )
    # One kg of additional concentrate is removed from the tailings per kg of
    # additional reference output. Concentrate grade and original allocation stay fixed.
    for exc in ds["exchanges"]:
        if exc["type"] != "production":
            factor = normalization
            if exc is tailings:
                factor *= remaining_mass / original_mass
            _change(
                report,
                ds,
                exc,
                factor,
                "Normalize original ore-throughput burdens to increased concentrate output",
            )
    _replace_provider(tailings, supplier)
    coarse_mass = (
        original_mass * params["coarse_tailings_mass_fraction"] * normalization
    )
    _additional(database, ds, target, report, multiplier=coarse_mass)
    report["derived_parameters"].append(
        {
            "activity": _identity(ds),
            "baseline_recovery": baseline,
            "downstream_recovery": downstream,
            "fraction_of_original_copper_loss_recovered": capture,
            "new_recovery": baseline + (1 - baseline) * capture,
            "additional_concentrate_per_original_kg": output_gain,
            "ore_throughput_multiplier": normalization,
            "coarse_tailings_processed_kg_per_kg_product": coarse_mass,
        }
    )
    report["limitations"].append(
        "Dexing fractions transferred using explicit target evidence; constant concentrate grade, allocation and leachable fractions. Other aqueous loads conserved per ore throughput; recovered gangue is assumed not to carry other modeled contaminants. pH/speciation and coproduct reallocation are not modeled. Residues return to the existing tailings treatment; additional inputs must exclude that already-counted disposal or represent net routing changes."
    )
    if scope == "yield_potential":
        report["limitations"].append(
            "Recovery yield-potential case excludes recovery/finishing burdens; not a complete recovery LCA."
        )


def apply_mining_sensitivity(
    database, case, targets, label=None, system_model="cutoff"
):
    """Return an independent database and an auditable report; never edit input.

    All targets are validated on the copy before returning. A failure leaves the
    original untouched. Caller must provide stage/collection allocations and their
    evidence, rather than silently transferring a percentage to the entire mine.
    """
    _require(case in CASES, f"Unknown sensitivity {case!r}; choose from {list(CASES)}")
    _require(
        system_model == "cutoff",
        "Mining sensitivity currently supports cut-off inventories only",
    )
    _require(
        isinstance(targets, list) and bool(targets),
        "Provide a nonempty list of exact activity targets",
    )
    label = label or case
    _require(
        isinstance(label, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", label) is not None,
        "Sensitivity labels must start with a letter/digit and contain only letters, digits, underscores, dots or hyphens",
    )
    sources = literature_sources()
    result = copy.deepcopy(database)
    _require(
        not any(ds.get("mining sensitivity") for ds in result),
        "Apply each case to an independent baseline; already perturbed inventories are rejected",
    )
    report = {
        "case": case,
        "label": label,
        "system_model": system_model,
        "source_id": CASES[case],
        "source": sources[CASES[case]],
        "configuration": copy.deepcopy(targets),
        "changes": [],
        "created_activities": [],
        "derived_parameters": [],
        "limitations": [],
        "target_count": len(targets),
    }
    selected = set()
    for target in targets:
        _keys(
            target,
            TARGET_FIELDS[case]
            | {"activity", "additional_exchanges", "additional_inventory_basis"},
            "target",
        )
        ds = _dataset(result, target["activity"])
        key = tuple(_identity(ds).values())
        _require(key not in selected, "The same activity is targeted more than once")
        selected.add(key)
        _require(
            "mine" in ds["name"].lower() or "tailing" in ds["name"].lower(),
            "Targets must be mining/beneficiation or tailings activities",
        )
        _unit_production(
            ds, -1 if ds["reference product"].startswith("sulfidic tailings") else 1
        )
        if case in ("grinding_best_practice", "hpgr"):
            _energy(result, ds, target, report["source"]["parameters"], report, case)
        elif case == "haulage_electrification":
            _electrify(result, ds, target, report, label)
        elif case == "drainage_treatment":
            _drainage(result, ds, target, report)
        else:
            _recovery(result, ds, target, report, label)
        ds["mining sensitivity"] = {
            "case": case,
            "label": label,
            "source": report["source"]["url"],
        }
        ds["comment"] = (
            ds.get("comment", "")
            + f"\nMining sensitivity {label}: {report['source']['url']}. See accompanying sensitivity report for allocations and limitations."
        )
    report["limitations"] = list(dict.fromkeys(report["limitations"]))
    report["changed_exchange_count"] = sum(
        c["before"] != c["after"] for c in report["changes"]
    )
    _require(report["changed_exchange_count"] > 0, "Sensitivity changed no exchanges")
    for ds in result:
        for exc in ds["exchanges"]:
            _require(math.isfinite(exc["amount"]), "Non-finite exchange produced")
    return SensitivityResult(result, _plain(report))


def inspect_mining_inventory(database):
    """List candidate inputs and releases, without guessing process allocations."""
    rows = []
    for ds in database:
        name = ds["name"].lower()
        if not ("mine operation" in name or "treatment of sulfidic tailings" in name):
            continue
        candidates = []
        for exc in ds["exchanges"]:
            if exc["type"] == "technosphere" and (
                exc.get("product", "").startswith(("electricity", "diesel"))
                or "tailing" in exc.get("product", "")
            ):
                candidates.append(
                    {
                        k: exc[k]
                        for k in (
                            "type",
                            "name",
                            "product",
                            "location",
                            "unit",
                            "amount",
                        )
                    }
                )
            elif exc["type"] == "biosphere" and exc["name"] in METALS:
                candidates.append(
                    {
                        k: exc[k]
                        for k in ("type", "name", "categories", "unit", "amount")
                    }
                )
        rows.append(
            {"activity": _identity(ds), "unit": ds["unit"], "candidates": candidates}
        )
    return rows
