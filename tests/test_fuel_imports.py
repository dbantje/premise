from pathlib import Path
from types import SimpleNamespace

import yaml
import xarray as xr

from premise.fuels.liquid_fuels import SyntheticFuelsMixin
from premise.transformation import BaseTransformation


ROOT = Path(__file__).resolve().parents[1]


def test_imported_market_suppliers_use_world_activity():
    obj = BaseTransformation.__new__(BaseTransformation)
    obj.iam_to_ecoinvent_loc = {"EUR": ["RER"]}

    world_supplier = {
        "name": "market for hydrogen, gaseous",
        "reference product": "hydrogen, gaseous",
        "location": "World",
        "unit": "kilogram",
    }
    regional_supplier = {
        "name": "market for hydrogen, gaseous",
        "reference product": "hydrogen, gaseous",
        "location": "RER",
        "unit": "kilogram",
    }

    suppliers = obj.select_market_suppliers(
        "hydrogen, imported", [regional_supplier, world_supplier], "EUR"
    )

    assert suppliers == [world_supplier]


def test_imported_market_suppliers_can_target_world_proxy():
    obj = BaseTransformation.__new__(BaseTransformation)
    obj.iam_to_ecoinvent_loc = {"EUR": ["RER"]}

    suppliers = obj.select_market_suppliers(
        "diesel, synthetic, imported",
        [
            {
                "name": "diesel production, synthetic",
                "reference product": "diesel, synthetic",
                "location": "RER",
                "unit": "kilogram",
            }
        ],
        "EUR",
    )

    assert suppliers == [
        {
            "name": "diesel production, synthetic",
            "reference product": "diesel, synthetic",
            "location": "World",
            "unit": "kilogram",
        }
    ]


def test_market_suppliers_fall_back_to_rer_when_no_regional_supplier():
    obj = BaseTransformation.__new__(BaseTransformation)
    obj.iam_to_ecoinvent_loc = {"CAZ": ["CA"]}

    rer_supplier = {
        "name": "ethanol production, via fermentation, from sugarbeet, energy allocation",
        "reference product": "ethanol",
        "location": "RER",
        "unit": "kilogram",
    }
    us_supplier = {
        "name": "ethanol production, via fermentation, from sugarcane, energy allocation",
        "reference product": "ethanol",
        "location": "US",
        "unit": "kilogram",
    }

    suppliers = obj.select_market_suppliers(
        "bioethanol, from sugar", [us_supplier, rer_supplier], "CAZ"
    )

    assert suppliers == [rer_supplier]


def test_imported_synfuel_proxy_requires_positive_production_volume():
    obj = SyntheticFuelsMixin.__new__(SyntheticFuelsMixin)
    obj.iam_data = SimpleNamespace(
        production_volumes=xr.DataArray(
            [[[0.0, 2.0]]],
            coords={
                "variables": [
                    "diesel, synthetic, imported",
                    "petrol, synthetic, imported",
                ],
                "region": ["EUR"],
                "year": [2050],
            },
            dims=("region", "year", "variables"),
        )
    )

    assert not obj._has_positive_production_volume("diesel, synthetic, imported")
    assert obj._has_positive_production_volume("petrol, synthetic, imported")
    assert not obj._has_positive_production_volume("hydrogen, imported")


def test_imported_fuel_yaml_entries_are_mapped_to_remind_import_variables():
    fuels = yaml.safe_load((ROOT / "premise/iam_variables_mapping/fuels.yaml").read_text())
    fuel_groups = yaml.safe_load((ROOT / "premise/data/fuels/fuel_groups.yaml").read_text())

    assert fuel_groups["hydrogen"][-1] == "hydrogen, imported"
    assert fuel_groups["diesel"][2] == "diesel, synthetic, imported"
    assert fuel_groups["gasoline"][-1] == "petrol, synthetic, imported"

    assert fuels["hydrogen, imported"]["iam_aliases"] == {
        "remind": "Trade|Imports|SE|Hydrogen",
        "remind-eu": "Trade|Imports|SE|Hydrogen",
    }
    assert fuels["diesel, synthetic, imported"]["iam_aliases"] == {
        "remind": "Trade|Imports|SE|Liquids|Hydrogen",
        "remind-eu": "Trade|Imports|SE|Liquids|Hydrogen",
    }
    assert fuels["petrol, synthetic, imported"]["iam_aliases"] == {
        "remind": "Trade|Imports|SE|Liquids|Hydrogen",
        "remind-eu": "Trade|Imports|SE|Liquids|Hydrogen",
    }

    assert (
        fuels["hydrogen, imported"]["ecoinvent_aliases"]["fltr"]["location"]
        == "RoW"
    )
    assert (
        fuels["diesel, synthetic, imported"]["ecoinvent_aliases"]["fltr"]["location"]
        == "RER"
    )
    assert (
        fuels["petrol, synthetic, imported"]["ecoinvent_aliases"]["fltr"]["location"]
        == "RER"
    )
