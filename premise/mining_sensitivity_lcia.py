"""Score mining sensitivity inventories without registering database copies.

Only changed activity columns and new mine-specific suppliers replace/extend
Brightway's baseline matrices. Baseline column reconstruction is checked before
using this adapter, to detect sign, mapping or allocation incompatibilities.
"""

import csv
import html
import json
import math
import warnings
from pathlib import Path

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import MatrixRankWarning, spsolve

from .mining_sensitivity import _dataset, _identity, _plain, _require

DEFAULT_METHOD_FAMILY = "ReCiPe 2016 v1.03, midpoint (H)"
DEFAULT_MINE_NAME = "copper mine operation and beneficiation, sulfide ore"
DEFAULT_MINE_PRODUCT = "copper concentrate, sulfide ore"


def select_methods(registry, family=DEFAULT_METHOD_FAMILY):
    methods = sorted(tuple(method) for method in registry if method[0] == family)
    _require(bool(methods), f"No LCIA methods installed for {family!r}")
    return methods


def _identity_tuple(ds):
    return ds["name"], ds["reference product"], ds["location"], ds["unit"]


def _selector(exc):
    keys = (
        ("name", "product", "location", "unit")
        if exc["type"] == "technosphere"
        else ("name", "categories", "unit")
    )
    return {key: exc[key] for key in keys}


def full_coverage_cases(
    database,
    location="CL",
    baseline_recovery=None,
    downstream_recovery=None,
    recovery_basis=None,
):
    """Explicit optimistic coverage bounds; never impute missing recovery data."""
    mine = _dataset(
        database,
        {
            "name": DEFAULT_MINE_NAME,
            "reference product": DEFAULT_MINE_PRODUCT,
            "location": location,
        },
    )
    electricity = [
        e
        for e in mine["exchanges"]
        if e["type"] == "technosphere"
        and e.get("product", "").startswith("electricity")
        and e["unit"] == "kilowatt hour"
        and e["amount"] > 0
    ]
    _require(bool(electricity), "Selected mine has no positive electricity input")
    coverage_basis = (
        "Conditional full-coverage technical-potential bound: apply published process performance "
        "to all selected modeled inputs. Coverage=1 is an analyst-defined endpoint, not a measured "
        "stage share or an adoption forecast."
    )
    cases, statuses = [], []
    for case, label in (
        ("grinding_best_practice", "BOUND-grinding"),
        ("hpgr", "BOUND-hpgr"),
    ):
        cases.append(
            {
                "case": case,
                "label": label,
                "targets": [
                    {
                        "activity": _identity(mine),
                        "electricity": [
                            {
                                "exchange": _selector(exc),
                                "stage_share": 1.0,
                                "basis": coverage_basis,
                            }
                            for exc in electricity
                        ],
                    }
                ],
            }
        )
    services = [
        e
        for e in mine["exchanges"]
        if e["type"] == "technosphere"
        and e.get("product") == "diesel, burned in building machine"
        and e["unit"] == "megajoule"
        and e["amount"] > 0
    ]
    if services:
        candidates = [
            e for e in electricity if e["product"] == "electricity, medium voltage"
        ]
        _require(
            len(candidates) == 1,
            "Select exactly one medium-voltage supplier for automatic electrification",
        )
        supply = candidates[0]
        cases.append(
            {
                "case": "haulage_electrification",
                "label": "BOUND-haulage",
                "targets": [
                    {
                        "activity": _identity(mine),
                        "electricity_supplier": {
                            "name": supply["name"],
                            "reference product": supply["product"],
                            "location": supply["location"],
                        },
                        "combustion_service_air_emissions": True,
                        "combustion_service_basis": "Selected ecoinvent diesel combustion service; its direct air releases are treated as exhaust. Original machinery and lubricants remain as proxies.",
                        "diesel_services": [
                            {
                                "exchange": _selector(e),
                                "haulage_share": 1.0,
                                "basis": coverage_basis,
                            }
                            for e in services
                        ],
                    }
                ],
            }
        )
    else:
        statuses.append(
            {
                "case": "haulage_electrification",
                "label": "BOUND-haulage",
                "status": "not_applicable",
                "reason": "No supported MJ building-machine combustion-service exchange; raw fuel and generator inputs are not silently converted.",
            }
        )
    treatment_matches = [
        ds
        for ds in database
        if ds["name"]
        == "treatment of sulfidic tailings, from copper mine operation, tailings impoundment"
        and ds["reference product"] == "sulfidic tailings, from copper mine operation"
        and ds["location"] == location
    ]
    if len(treatment_matches) == 1:
        treatment = treatment_matches[0]
        releases = [
            e
            for e in treatment["exchanges"]
            if e["type"] == "biosphere"
            and e["name"] in ("Copper ion", "Zinc II", "Cadmium II")
            and tuple(e.get("categories", ()))[:1] == ("water",)
            and not any("long-term" in c or "long term" in c for c in e["categories"])
            and e["amount"] > 0
        ]
        if releases:
            for period in ("second", "third"):
                cases.append(
                    {
                        "case": "drainage_treatment",
                        "label": f"BOUND-drainage-{period}",
                        "targets": [
                            {
                                "activity": _identity(treatment),
                                "performance": period,
                                "scope": "emission_potential",
                                "emissions": [
                                    {
                                        "exchange": _selector(e),
                                        "collected_fraction": 1.0,
                                        "basis": coverage_basis
                                        + " All modeled short-term releases are assumed capturable and treated; operational duration and collection infrastructure are not established.",
                                    }
                                    for e in releases
                                ],
                            }
                        ],
                    }
                )
        else:
            statuses.append(
                {
                    "case": "drainage_treatment",
                    "label": "BOUND-drainage",
                    "status": "not_applicable",
                    "reason": "No eligible short-term Cu/Zn/Cd releases",
                }
            )
    else:
        statuses.append(
            {
                "case": "drainage_treatment",
                "label": "BOUND-drainage",
                "status": "needs_inputs",
                "reason": "Exact local copper tailings impoundment activity is missing or ambiguous",
            }
        )
    supplied = [
        baseline_recovery is not None,
        downstream_recovery is not None,
        bool(recovery_basis),
    ]
    _require(
        not any(supplied) or all(supplied),
        "Copper recovery requires --baseline-recovery, --downstream-recovery and --recovery-basis together",
    )
    for performance in ("low", "high"):
        label = f"BOUND-recovery-{performance}"
        if not all(supplied):
            statuses.append(
                {
                    "case": "copper_recovery",
                    "label": label,
                    "status": "needs_inputs",
                    "reason": "Supply sourced --baseline-recovery, --downstream-recovery and --recovery-basis; baseline recovery is not inferred from allocated inventories.",
                }
            )
            continue
        tails = [
            e
            for e in mine["exchanges"]
            if e["type"] == "technosphere"
            and e.get("product") == "sulfidic tailings, from copper mine operation"
            and e["unit"] == "kilogram"
            and e["amount"] < 0
        ]
        _require(
            len(tails) == 1,
            "Automatic recovery requires one local copper-tailings waste exchange",
        )
        cases.append(
            {
                "case": "copper_recovery",
                "label": label,
                "targets": [
                    {
                        "activity": _identity(mine),
                        "performance": performance,
                        "baseline_recovery": baseline_recovery,
                        "baseline_recovery_basis": recovery_basis,
                        "downstream_recovery": downstream_recovery,
                        "downstream_recovery_basis": recovery_basis,
                        "transfer_basis": "Conditional Dexing-equivalent coarse-tailings size/loss distribution; published fractions are transferred as a named technology case, not claimed as observations at this mine.",
                        "tailings_model": "constant_leachable_fraction",
                        "scope": "yield_potential",
                        "tailings": _selector(tails[0]),
                    }
                ],
            }
        )
    return cases, statuses


class MiningSensitivityLCA:
    """An LCIA session with one baseline LCI and reusable method vectors."""

    def __init__(self, database, demand, methods):
        import bw2calc as bc
        import bw2data as bd

        _require(bool(methods), "Select at least one LCIA method")
        for method in methods:
            _require(method in bd.methods, f"LCIA method is not installed: {method}")
        self.original = database
        self.lca = bc.LCA(demand, methods[0])
        self.lca.lci()
        self.key_to_id = {}
        self.original_keys = {(ds["database"], ds["code"]) for ds in database}
        self.methods = []
        self.method_vectors = []
        for method in methods:
            self.lca.switch_method(method)
            self.lca.lcia()
            _require(
                math.isfinite(float(self.lca.score)),
                f"Non-finite baseline score for {method}",
            )
            self.methods.append(
                {
                    "method": list(method),
                    "unit": bd.methods[method].get("unit", ""),
                    "baseline": float(self.lca.score),
                }
            )
            self.method_vectors.append(
                np.asarray(self.lca.characterization_matrix.sum(axis=0)).ravel()
            )
        self.method_vectors = np.vstack(self.method_vectors)
        self.demand = np.array(self.lca.demand_array, copy=True)
        self._validated_columns = set()
        self.last_residual = None

    def _node_id(self, key):
        if key not in self.key_to_id:
            import bw2data as bd

            self.key_to_id[key] = bd.get_node(database=key[0], code=key[1]).id
        return self.key_to_id[key]

    def _columns(self, ds, suppliers, new_products, new_activities):
        key = ds["database"], ds["code"]
        col = (
            new_activities[key]
            if key in new_activities
            else self.lca.dicts.activity[self._node_id(key)]
        )
        row = (
            new_products[key]
            if key in new_products
            else self.lca.dicts.product[self._node_id(key)]
        )
        tech, bio = {}, {}
        production = [e for e in ds["exchanges"] if e["type"] == "production"]
        _require(
            len(production) == 1,
            "LCIA adapter requires one reference production exchange",
        )
        tech[row] = float(production[0]["amount"])
        for exc in ds["exchanges"]:
            amount = float(exc["amount"])
            _require(math.isfinite(amount), "Non-finite inventory coefficient")
            if exc["type"] == "production" or amount == 0:
                continue
            if exc["type"] == "biosphere":
                _require(
                    bool(exc.get("input")), "LCIA requires linked biosphere exchanges"
                )
                flow_id = self._node_id(tuple(exc["input"]))
                _require(
                    flow_id in self.lca.dicts.biosphere,
                    "New biosphere flow is outside baseline mapping; use registered-database LCIA",
                )
                index = self.lca.dicts.biosphere[flow_id]
                bio[index] = bio.get(index, 0) + amount
            elif exc["type"] == "technosphere":
                if exc.get("input"):
                    provider = tuple(exc["input"])
                else:
                    identity = exc["name"], exc["product"], exc["location"], exc["unit"]
                    matches = suppliers.get(identity, [])
                    _require(
                        len(matches) == 1,
                        f"Ambiguous or missing LCIA supplier: {identity}",
                    )
                    provider = matches[0]
                index = (
                    new_products[provider]
                    if provider in new_products
                    else self.lca.dicts.product[self._node_id(provider)]
                )
                tech[index] = tech.get(index, 0) - amount
            else:
                raise ValueError(
                    f"Unsupported exchange type for LCIA adapter: {exc['type']}"
                )
        return col, tech, bio

    @staticmethod
    def _check_column(matrix, column, values):
        old = matrix[:, column].tocoo()
        existing = {int(row): float(value) for row, value in zip(old.row, old.data)}
        for row in set(existing) | set(values):
            _require(
                math.isclose(
                    existing.get(row, 0),
                    values.get(row, 0),
                    rel_tol=2e-7,
                    abs_tol=1e-14,
                ),
                "Baseline inventory/matrix mismatch; refusing an incompatible in-memory calculation",
            )

    @staticmethod
    def _replace_columns(matrix, replacements, shape):
        original = matrix.tocoo()
        keep = ~np.isin(original.col, list(replacements))
        rows, cols, values = (
            [original.row[keep]],
            [original.col[keep]],
            [original.data[keep]],
        )
        for col, column in replacements.items():
            rows.append(np.array(list(column), dtype=int))
            cols.append(np.full(len(column), col, dtype=int))
            values.append(np.array(list(column.values()), dtype=float))
        result = sparse.coo_matrix(
            (np.concatenate(values), (np.concatenate(rows), np.concatenate(cols))),
            shape=shape,
        ).tocsr()
        result.sum_duplicates()
        result.eliminate_zeros()
        return result

    def score(self, result):
        added = [
            ds
            for ds in result.database
            if (ds["database"], ds["code"]) not in self.original_keys
        ]
        new_products = {
            (ds["database"], ds["code"]): self.lca.technosphere_matrix.shape[0] + i
            for i, ds in enumerate(added)
        }
        new_activities = {
            (ds["database"], ds["code"]): self.lca.technosphere_matrix.shape[1] + i
            for i, ds in enumerate(added)
        }
        suppliers = {}
        for ds in result.database:
            suppliers.setdefault(_identity_tuple(ds), []).append(
                (ds["database"], ds["code"])
            )
        changed = [
            _dataset(result.database, target["activity"])
            for target in result.report["configuration"]
        ]
        for target in result.report["configuration"]:
            original = _dataset(self.original, target["activity"])
            key = original["database"], original["code"]
            if key not in self._validated_columns:
                col, tech, bio = self._columns(original, suppliers, {}, {})
                self._check_column(self.lca.technosphere_matrix, col, tech)
                self._check_column(self.lca.biosphere_matrix, col, bio)
                self._validated_columns.add(key)
        tech_columns, bio_columns = {}, {}
        for ds in changed + added:
            col, tech, bio = self._columns(ds, suppliers, new_products, new_activities)
            tech_columns[col], bio_columns[col] = tech, bio
        n_rows = self.lca.technosphere_matrix.shape[0] + len(added)
        n_cols = self.lca.technosphere_matrix.shape[1] + len(added)
        matrix = self._replace_columns(
            self.lca.technosphere_matrix, tech_columns, (n_rows, n_cols)
        )
        biosphere = self._replace_columns(
            self.lca.biosphere_matrix,
            bio_columns,
            (self.lca.biosphere_matrix.shape[0], n_cols),
        )
        demand = np.pad(self.demand, (0, len(added)))
        with warnings.catch_warnings():
            warnings.simplefilter("error", MatrixRankWarning)
            supply = spsolve(matrix, demand)
        _require(np.isfinite(supply).all(), "Non-finite supply vector")
        residual = np.max(np.abs(matrix @ supply - demand)) / max(
            1.0, np.max(np.abs(demand))
        )
        _require(residual < 1e-7, f"LCI residual too large: {residual}")
        self.last_residual = float(residual)
        scores = self.method_vectors @ np.asarray(biosphere @ supply).ravel()
        _require(np.isfinite(scores).all(), "Non-finite LCIA scores")
        return [float(value) for value in scores]


def score_rows(label, case, values, methods, functional_unit, purpose, limitations=()):
    rows = []
    for score, method in zip(values, methods):
        baseline = method["baseline"]
        rows.append(
            {
                "scenario": label,
                "case": case,
                "purpose": purpose,
                "activity": functional_unit["name"],
                "reference_product": functional_unit["reference product"],
                "location": functional_unit["location"],
                "functional_unit_amount": functional_unit["amount"],
                "functional_unit_unit": functional_unit["unit"],
                "method": json.dumps(method["method"]),
                "category": method["method"][1],
                "indicator": method["method"][-1],
                "impact_unit": method["unit"],
                "score": score,
                "baseline_score": baseline,
                "absolute_change": score - baseline,
                "percent_change": (
                    None if baseline == 0 else 100 * (score - baseline) / abs(baseline)
                ),
                "limitations": " | ".join(limitations),
            }
        )
    return rows


def write_score_outputs(output, rows, statuses, metadata):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "scores.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    change_fields = (
        "scenario",
        "case",
        "category",
        "indicator",
        "impact_unit",
        "baseline_score",
        "score",
        "absolute_change",
        "percent_change",
        "purpose",
        "limitations",
    )
    with (output / "changes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=change_fields)
        writer.writeheader()
        writer.writerows(
            {key: row[key] for key in change_fields}
            for row in rows
            if row["case"] != "baseline"
        )
    with (output / "case-status.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=("case", "label", "status", "reason")
        )
        writer.writeheader()
        writer.writerows(statuses)
    (output / "run.json").write_text(
        json.dumps(_plain(metadata), indent=2) + "\n", encoding="utf-8"
    )
    scenarios = list(dict.fromkeys(row["scenario"] for row in rows))
    categories = list(dict.fromkeys(row["category"] for row in rows))
    lookup = {(row["scenario"], row["category"]): row for row in rows}
    cells = []
    for category in categories:
        first = lookup[(scenarios[0], category)]
        line = [
            f"<th>{html.escape(category)}<small>{html.escape(first['impact_unit'])}</small></th>"
        ]
        for scenario in scenarios:
            row = lookup[(scenario, category)]
            percent = row["percent_change"]
            change = (
                "baseline"
                if row["case"] == "baseline"
                else ("undefined %" if percent is None else f"{percent:+.2f}%")
            )
            line.append(f"<td>{row['score']:.6g}<small>{change}</small></td>")
        cells.append("<tr>" + "".join(line) + "</tr>")
    fu = metadata["functional_unit"]
    explanation = metadata["purpose"]
    skipped = "".join(
        f"<li>{html.escape(s['label'])}: {html.escape(s['reason'])}</li>"
        for s in statuses
        if s["status"] != "calculated"
    )
    limits = "".join(
        f"<li>{html.escape(label)}: {html.escape(' | '.join(metadata['limitations'].get(label, [])))}</li>"
        for label in scenarios[1:]
    )
    document = (
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Mining sensitivity LCIA</title>"
        "<style>body{font:15px system-ui;margin:32px;color:#17232c}table{border-collapse:collapse;font-variant-numeric:tabular-nums}"
        "th,td{padding:10px;border:1px solid #d4dde3;text-align:right}th:first-child{text-align:left}small{display:block;color:#52616d;font-size:12px}"
        "thead{background:#edf2f5}p,li{max-width:1100px}li{margin:8px 0}.scroll{overflow-x:auto}</style>"
        f"<h1>Mining sensitivity LCIA</h1><p>{html.escape(explanation)}</p>"
        f"<p>Functional unit: {fu['amount']:g} {html.escape(fu['unit'])} of {html.escape(fu['reference product'])}, {html.escape(fu['location'])}. "
        f"Activity: {html.escape(fu['name'])}.</p><p>Source: {html.escape(metadata['project'])} / {html.escape(metadata['source_database'])}. "
        "Changes compare identical demand and LCIA methods. Blank percentages indicate a zero baseline.</p>"
        "<div class='scroll'><table><thead><tr><th>Indicator / unit</th>"
        + "".join(f"<th>{html.escape(scenario)}</th>" for scenario in scenarios)
        + "</tr></thead><tbody>"
        + "".join(cells)
        + "</tbody></table></div>"
        + (f"<h2>Cases needing input</h2><ul>{skipped}</ul>" if skipped else "")
        + f"<h2>Case boundaries</h2><ul>{limits}</ul>"
        + "<p>Sources and full configurations are recorded in the accompanying JSON reports. CSV files contain unrounded scores.</p></html>"
    )
    (output / "summary.html").write_text(document, encoding="utf-8")
