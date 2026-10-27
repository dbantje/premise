"""Mass, energy, isolation and uncertainty checks without proprietary inventories."""

import copy
import math

import pytest

from premise.mining_sensitivity import apply_mining_sensitivity, literature_sources


def activity(name, product, unit="kilogram", production=1, location="CL", exchanges=()):
    return {
        "name": name,
        "reference product": product,
        "location": location,
        "unit": unit,
        "database": "test",
        "code": name,
        "exchanges": [
            {
                "type": "production",
                "name": name,
                "product": product,
                "location": location,
                "unit": unit,
                "amount": production,
                "input": ("test", name),
            }
        ]
        + list(copy.deepcopy(exchanges)),
    }


def tech(ds, amount, **kwargs):
    return {
        "type": "technosphere",
        "name": ds["name"],
        "product": ds["reference product"],
        "location": ds["location"],
        "unit": ds["unit"],
        "amount": amount,
        "input": ("test", ds["code"]),
        **kwargs,
    }


def bio(name, amount, categories=("water", "surface water"), **kwargs):
    return {
        "type": "biosphere",
        "name": name,
        "categories": categories,
        "unit": "kilogram",
        "amount": amount,
        "input": ("biosphere", name),
        **kwargs,
    }


def identity(ds):
    return {k: ds[k] for k in ("name", "reference product", "location")}


def selector(exc):
    fields = (
        ("name", "product", "location", "unit")
        if exc["type"] == "technosphere"
        else ("name", "categories", "unit")
    )
    return {k: exc[k] for k in fields}


@pytest.fixture
def inventory():
    electricity = activity(
        "market for electricity, medium voltage",
        "electricity, medium voltage",
        "kilowatt hour",
    )
    diesel = activity("market for diesel", "diesel", location="GLO")
    machinery = activity(
        "market for building machine", "building machine", "unit", location="GLO"
    )
    service = activity(
        "diesel, burned in building machine",
        "diesel, burned in building machine",
        "megajoule",
        location="GLO",
        exchanges=[
            tech(diesel, 0.0234),
            tech(machinery, 1e-7),
            bio("Carbon dioxide, fossil", 0.075, ("air",)),
            bio("Nitrogen oxides", 0.001, ("air",)),
            bio("Oil", 0.00001, ("soil",)),
        ],
    )
    market = activity(
        "market for diesel, burned in building machine",
        service["reference product"],
        "megajoule",
        location="GLO",
        exchanges=[tech(service, 1)],
    )
    tailings = activity(
        "treatment of sulfidic tailings, from copper mine operation, tailings impoundment",
        "sulfidic tailings, from copper mine operation",
        production=-1,
        exchanges=[
            bio("Copper ion", 0.0001),
            bio("Zinc II", 0.0002),
            bio("Cadmium II", 0.00001),
            bio("Copper ion", 0.0003, ("water", "ground-, long-term")),
            bio("Occupation, dump site", 0.01, ("natural resource", "land")),
        ],
    )
    tail_market = activity(
        "market for sulfidic tailings, from copper mine operation",
        tailings["reference product"],
        production=-1,
        exchanges=[tech(tailings, -1)],
    )
    mine = activity(
        "copper mine operation and beneficiation, sulfide ore",
        "copper concentrate, sulfide ore",
        exchanges=[
            tech(
                electricity,
                10,
                **{"uncertainty type": 2, "loc": math.log(10), "scale": 0.2},
            ),
            tech(market, 20),
            tech(tail_market, -50),
            bio("Copper", 0.3, ("natural resource", "in ground")),
        ],
    )
    other = activity(
        "unrelated consumer",
        "other",
        exchanges=[tech(market, 2), tech(tail_market, -3)],
    )
    return [
        electricity,
        diesel,
        machinery,
        service,
        market,
        tailings,
        tail_market,
        mine,
        other,
    ]


def energy_target(db):
    return {
        "activity": identity(db[-2]),
        "electricity": [
            {
                "exchange": selector(db[-2]["exchanges"][1]),
                "stage_share": 0.6,
                "basis": "Synthetic fixture: 6 of 10 kWh belong to the milling machine",
            }
        ],
    }


@pytest.mark.parametrize(
    "case,reduction", [("grinding_best_practice", 0.15), ("hpgr", 0.62)]
)
def test_energy_stage_boundary_and_uncertainty(inventory, case, reduction):
    original = copy.deepcopy(inventory)
    result = apply_mining_sensitivity(inventory, case, [energy_target(inventory)])
    exchange = result.database[-2]["exchanges"][1]
    expected = 10 * (1 - 0.6 * reduction)
    assert exchange["amount"] == pytest.approx(expected)
    assert math.exp(exchange["loc"]) == pytest.approx(expected)
    assert exchange["scale"] == 0.2
    assert result.database[-2]["exchanges"][0]["amount"] == 1
    assert inventory == original
    assert result.report["source"]["url"].startswith("https://")


@pytest.mark.parametrize("share", [None, -1, 0, 1.1, float("nan"), True])
def test_bad_stage_share_is_rejected(inventory, share):
    target = energy_target(inventory)
    target["electricity"][0]["stage_share"] = share
    with pytest.raises(ValueError):
        apply_mining_sensitivity(inventory, "hpgr", [target])


def test_stage_evidence_required_and_failure_is_atomic(inventory):
    original = copy.deepcopy(inventory)
    target = energy_target(inventory)
    target["electricity"][0]["basis"] = ""
    with pytest.raises(ValueError, match="basis"):
        apply_mining_sensitivity(inventory, "hpgr", [target])
    assert inventory == original


def test_ambiguous_or_wrong_stage_exchanges_fail(inventory):
    target = energy_target(inventory)
    inventory[-2]["exchanges"].append(copy.deepcopy(inventory[-2]["exchanges"][1]))
    with pytest.raises(ValueError, match="found 2"):
        apply_mining_sensitivity(inventory, "hpgr", [target])


def haulage_target(db):
    return {
        "activity": identity(db[-2]),
        "electricity_supplier": identity(db[0]),
        "combustion_service_air_emissions": True,
        "combustion_service_basis": "Synthetic service air flows are exclusively exhaust",
        "diesel_services": [
            {
                "exchange": selector(db[-2]["exchanges"][2]),
                "haulage_share": 0.75,
                "basis": "Synthetic duty allocation",
            }
        ],
    }


def test_electrification_isolates_market_and_retains_equipment(inventory):
    original = copy.deepcopy(inventory)
    result = apply_mining_sensitivity(
        inventory, "haulage_electrification", [haulage_target(inventory)]
    )
    assert inventory == original
    assert result.database[3] == original[3]  # global combustion supplier
    assert result.database[4] == original[4]  # global market
    assert result.database[8] == original[8]  # other consumer
    mine = result.database[7]
    assert mine["exchanges"][2]["amount"] == 5
    assert mine["exchanges"][-1]["amount"] == 15
    assert "input" not in mine["exchanges"][-1]
    service = next(
        ds for ds in result.database[9:] if ds["name"].startswith("diesel, burned")
    )
    assert service["exchanges"][0]["amount"] == 1
    assert "input" not in service["exchanges"][0]
    assert service["exchanges"][1]["amount"] == 0
    assert service["exchanges"][2]["amount"] == 1e-7
    assert service["exchanges"][3]["amount"] == 0
    assert service["exchanges"][4]["amount"] == 0
    assert service["exchanges"][5]["amount"] == 0.00001
    assert service["exchanges"][-1]["amount"] == pytest.approx(0.35 / 0.9 / 3.6)


def drainage_target(db, performance="third"):
    return {
        "activity": identity(db[5]),
        "performance": performance,
        "scope": "emission_potential",
        "emissions": [
            {
                "exchange": selector(e),
                "collected_fraction": 0.8,
                "basis": "Synthetic short-term captured drainage",
            }
            for e in db[5]["exchanges"][1:4]
        ],
    }


@pytest.mark.parametrize("period", ["second", "third"])
def test_drainage_uses_metal_specific_efficiencies_and_capture(inventory, period):
    result = apply_mining_sensitivity(
        inventory, "drainage_treatment", [drainage_target(inventory, period)]
    )
    efficiencies = literature_sources()["hustwit_sykes_1994"]["parameters"][period]
    for i, metal in enumerate(("copper", "zinc", "cadmium"), 1):
        assert result.database[5]["exchanges"][i]["amount"] == pytest.approx(
            inventory[5]["exchanges"][i]["amount"] * (1 - 0.8 * efficiencies[metal])
        )
    assert result.database[5]["exchanges"][4] == inventory[5]["exchanges"][4]
    assert result.report["limitations"]


def test_drainage_rejects_long_term_and_resource_flows(inventory):
    target = drainage_target(inventory)
    target["emissions"][0]["exchange"] = selector(inventory[5]["exchanges"][4])
    with pytest.raises(ValueError, match="Long-term"):
        apply_mining_sensitivity(inventory, "drainage_treatment", [target])
    target["emissions"][0]["exchange"] = selector(inventory[5]["exchanges"][5])
    with pytest.raises(ValueError, match="aqueous"):
        apply_mining_sensitivity(inventory, "drainage_treatment", [target])


def test_drainage_does_not_double_apply_baseline_treatment(inventory):
    target = drainage_target(inventory)
    target["emissions"] = [target["emissions"][0]]
    target["emissions"][0].update(
        {
            "baseline_removal": 0.981,
            "baseline_removal_basis": "Published second operational state",
        }
    )
    result = apply_mining_sensitivity(inventory, "drainage_treatment", [target])
    assert result.database[5]["exchanges"][1]["amount"] == pytest.approx(
        0.0001 * (0.2 + 0.8 * 0.005 / 0.019)
    )


def recovery_target(db, performance="low"):
    return {
        "activity": identity(db[-2]),
        "performance": performance,
        "baseline_recovery": 0.9,
        "baseline_recovery_basis": "Synthetic ore model",
        "downstream_recovery": 0.8,
        "downstream_recovery_basis": "Synthetic finishing model",
        "transfer_basis": "Synthetic fixture has the published Dexing size distribution",
        "tailings_model": "constant_leachable_fraction",
        "scope": "yield_potential",
        "tailings": selector(db[-2]["exchanges"][3]),
    }


@pytest.mark.parametrize("performance,recovery", [("low", 0.55), ("high", 0.65)])
def test_recovery_preserves_mass_and_non_copper_leachate(
    inventory, performance, recovery
):
    result = apply_mining_sensitivity(
        inventory, "copper_recovery", [recovery_target(inventory, performance)]
    )
    capture = 0.68 * recovery * 0.8
    gain = (1 - 0.9) / 0.9 * capture
    normalization = 1 / (1 + gain)
    mine = result.database[7]
    assert mine["exchanges"][0]["amount"] == 1
    assert mine["exchanges"][1]["amount"] == pytest.approx(10 * normalization)
    assert mine["exchanges"][3]["amount"] == pytest.approx(-(50 - gain) * normalization)
    assert 1 + abs(mine["exchanges"][3]["amount"]) == pytest.approx(51 * normalization)
    treatment = next(
        ds for ds in result.database[9:] if ds["name"].startswith("treatment")
    )
    mass = abs(mine["exchanges"][3]["amount"])
    assert treatment["exchanges"][2]["amount"] * mass == pytest.approx(
        0.0002 * 50 * normalization
    )
    assert treatment["exchanges"][1]["amount"] * mass == pytest.approx(
        0.0001 * 50 * normalization * (1 - capture)
    )
    assert result.database[5] == inventory[5]
    assert result.database[8] == inventory[8]


def test_full_process_scopes_require_inventory(inventory):
    target = drainage_target(inventory)
    target["scope"] = "with_treatment_inventory"
    with pytest.raises(ValueError, match="Supply treatment"):
        apply_mining_sensitivity(inventory, "drainage_treatment", [target])
    target = recovery_target(inventory)
    target["scope"] = "with_recovery_inventory"
    with pytest.raises(ValueError, match="Supply a recovery"):
        apply_mining_sensitivity(inventory, "copper_recovery", [target])


def test_added_recovery_burdens_are_per_kg_coarse_tailings(inventory):
    target = recovery_target(inventory)
    target.update(
        {
            "scope": "with_recovery_inventory",
            "additional_inventory_basis": "Synthetic measured inputs",
            "additional_exchanges": [
                {"supplier": identity(inventory[0]), "amount": 0.2}
            ],
        }
    )
    result = apply_mining_sensitivity(inventory, "copper_recovery", [target])
    expected = (
        result.report["derived_parameters"][0][
            "coarse_tailings_processed_kg_per_kg_product"
        ]
        * 0.2
    )
    assert result.database[7]["exchanges"][-1]["amount"] == pytest.approx(expected)


def test_repeated_application_and_wrong_system_model_rejected(inventory):
    target = energy_target(inventory)
    result = apply_mining_sensitivity(inventory, "hpgr", [target])
    with pytest.raises(ValueError, match="independent baseline"):
        apply_mining_sensitivity(result.database, "hpgr", [target])
    with pytest.raises(ValueError, match="cut-off"):
        apply_mining_sensitivity(
            inventory, "hpgr", [target], system_model="consequential"
        )


def test_missing_activity_and_duplicate_targets_fail(inventory):
    target = energy_target(inventory)
    with pytest.raises(ValueError, match="more than once"):
        apply_mining_sensitivity(inventory, "hpgr", [target, target])
    target["activity"]["location"] = "missing"
    with pytest.raises(ValueError, match="found 0"):
        apply_mining_sensitivity(inventory, "hpgr", [target])


def test_newdatabase_fork_preserves_baseline_and_names(inventory):
    from premise.new_database import NewDatabase
    from premise.utils import create_scenario_list, eidb_label

    baseline = NewDatabase.__new__(NewDatabase)
    baseline.system_model = "cutoff"
    baseline.database = []
    baseline.scenarios = [
        {
            "model": "remind",
            "pathway": "test",
            "year": 2050,
            "database": inventory,
            "applied functions": ["metals"],
            "cache": {"old": 1},
            "index": {"old": 2},
        }
    ]
    original = copy.deepcopy(inventory)
    variant = baseline.with_mining_sensitivity(
        "hpgr", lambda s: [energy_target(s["database"])]
    )
    assert baseline.scenarios[0]["database"] == original
    assert baseline.scenarios[0]["index"] == {"old": 2}
    assert variant.scenarios[0]["index"] == {}
    assert variant.scenarios[0]["pathway"] == "test"
    assert "mining-hpgr" in eidb_label(variant.scenarios[0], "3.10")
    assert "mining hpgr" in create_scenario_list(variant.scenarios)[0]
    assert variant.mining_sensitivity_reports[0]["baseline"]["year"] == 2050
    with pytest.raises(ValueError, match="before"):
        variant.update("electricity")
    with pytest.raises(ValueError, match="independent"):
        variant.with_mining_sensitivity("hpgr", [])


def test_cached_fork_restores_metadata_and_survives_export_cache_cleanup(
    inventory, tmp_path
):
    import pickle
    from premise.new_database import NewDatabase

    baseline = NewDatabase.__new__(NewDatabase)
    baseline.system_model = "cutoff"
    baseline.database = []
    paths = [
        tmp_path / name
        for name in ("inventory.pickle", "source.pickle", "imports.pickle")
    ]
    paths[0].write_bytes(pickle.dumps(inventory))
    paths[1].write_bytes(
        pickle.dumps(
            {
                tuple(identity(inventory[7]).values()): {
                    "classifications": [("test", "mining")],
                    "parameters": {"documented": 1},
                }
            }
        )
    )
    paths[2].write_bytes(pickle.dumps({}))
    baseline.scenarios = [
        {
            "model": "remind",
            "pathway": "test",
            "year": 2050,
            "database filepath": paths[0],
            "database metadata cache filepath": paths[1],
            "inventories metadata cache filepath": paths[2],
            "applied functions": ["metals"],
        }
    ]
    target = energy_target(inventory)
    first = baseline.with_mining_sensitivity("hpgr", [target])
    assert first.scenarios[0]["database"][7]["parameters"] == {"documented": 1}
    assert first.scenarios[0]["database"][7]["classifications"] == [("test", "mining")]
    assert paths[0].exists()
    paths[0].unlink()  # ordinary export cache cleanup
    second = baseline.with_mining_sensitivity("grinding_best_practice", [target])
    assert second.scenarios[0]["database"][7]["parameters"] == {"documented": 1}
    assert baseline.scenarios[0]["database"][7]["exchanges"][1]["amount"] == 10


def test_unknown_configuration_keys_fail(inventory):
    target = energy_target(inventory)
    target["electricty"] = []
    with pytest.raises(ValueError, match="Unknown target"):
        apply_mining_sensitivity(inventory, "hpgr", [target])


def test_report_serializes_premise_float32_cache(inventory, tmp_path):
    import json
    import numpy as np

    for ds in inventory:
        for exc in ds["exchanges"]:
            exc["amount"] = np.float32(exc["amount"])
    target = energy_target(inventory)
    target["electricity"][0]["stage_share"] = np.float32(0.6)
    result = apply_mining_sensitivity(inventory, "hpgr", [target])
    json.dumps(result.report, allow_nan=False)
    result.write_report(tmp_path / "report.json")
    assert (
        json.loads((tmp_path / "report.json").read_text())["changed_exchange_count"]
        == 1
    )


def _solve_inventory(data, demand_index):
    """Independently solve a small signed technosphere for emission totals."""
    import numpy as np

    index = {
        (ds["name"], ds["reference product"], ds["location"], ds["unit"]): i
        for i, ds in enumerate(data)
    }
    matrix = np.zeros((len(data), len(data)))
    for j, ds in enumerate(data):
        for exc in ds["exchanges"]:
            if exc["type"] == "production":
                matrix[j, j] += exc["amount"]
            elif exc["type"] == "technosphere":
                i = index[(exc["name"], exc["product"], exc["location"], exc["unit"])]
                matrix[i, j] -= exc["amount"]
    demand = np.zeros(len(data))
    demand[demand_index] = 1
    supply = np.linalg.solve(matrix, demand)
    emissions = {}
    for amount, ds in zip(supply, data):
        for exc in ds["exchanges"]:
            if exc["type"] == "biosphere":
                key = exc["name"], tuple(exc["categories"])
                emissions[key] = emissions.get(key, 0) + amount * exc["amount"]
    return supply, emissions


def test_signed_waste_chain_lci_and_recovery_conservation(inventory):
    original_supply, original_emissions = _solve_inventory(inventory, 7)
    result = apply_mining_sensitivity(
        inventory, "copper_recovery", [recovery_target(inventory)]
    )
    supply, emissions = _solve_inventory(result.database, 7)
    normalization = result.report["derived_parameters"][0]["ore_throughput_multiplier"]
    recovered = result.report["derived_parameters"][0][
        "fraction_of_original_copper_loss_recovered"
    ]
    assert original_supply[5] == pytest.approx(50)
    for compartment in (("water", "surface water"), ("water", "ground-, long-term")):
        assert emissions[("Copper ion", compartment)] == pytest.approx(
            original_emissions[("Copper ion", compartment)]
            * normalization
            * (1 - recovered)
        )
    assert emissions[("Zinc II", ("water", "surface water"))] == pytest.approx(
        original_emissions[("Zinc II", ("water", "surface water"))] * normalization
    )
    assert supply[3] == pytest.approx(20 * normalization)


def test_electrification_lci_displaces_only_haulage_exhaust(inventory):
    result = apply_mining_sensitivity(
        inventory, "haulage_electrification", [haulage_target(inventory)]
    )
    supply, emissions = _solve_inventory(result.database, 7)
    assert supply[0] == pytest.approx(10 + 15 * 0.35 / 0.9 / 3.6)
    assert supply[1] == pytest.approx(5 * 0.0234)
    assert supply[2] == pytest.approx(20 * 1e-7)
    assert emissions[("Carbon dioxide, fossil", ("air",))] == pytest.approx(5 * 0.075)
    assert emissions[("Oil", ("soil",))] == pytest.approx(20 * 0.00001)
