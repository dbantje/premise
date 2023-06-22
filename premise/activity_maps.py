"""
activity_maps.py contains InventorySet, which is a class that provides all necessary
mapping between ``premise`` and ``ecoinvent`` terminology.
"""

import csv
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from pprint import pprint
from typing import List, Union

import yaml
from wurst import searching as ws

from . import DATA_DIR, VARIABLES_DIR
from .data_collection import get_delimiter

POWERPLANT_TECHS = VARIABLES_DIR / "electricity_variables.yaml"
FUELS_TECHS = VARIABLES_DIR / "fuels_variables.yaml"
MATERIALS_TECHS = DATA_DIR / "utils" / "materials_vars.yml"
DAC_TECHS = VARIABLES_DIR / "direct_air_capture_variables.yaml"
CARBON_STORAGE_TECHS = VARIABLES_DIR / "carbon_storage_variables.yaml"
CEMENT_TECHS = VARIABLES_DIR / "cement_variables.yaml"
GAINS_MAPPING = (
    DATA_DIR / "GAINS_emission_factors" / "gains_ecoinvent_sectoral_mapping.yaml"
)
ACTIVITIES_METALS_MAPPING = DATA_DIR / "metals" / "activities_mapping.yml"
METALS_MAPPING = DATA_DIR / "metals" / "metals_mapping.yml"


def get_mapping(filepath: Path, var: str) -> dict:
    """
    Loa a YAML file and return a dictionary given a variable.
    :param filepath: YAML file path
    :param var: variable to return the dictionary for.
    :return: a dictionary
    """

    with open(filepath, "r", encoding="utf-8") as stream:
        techs = yaml.full_load(stream)

    mapping = {}
    for key, val in techs.items():
        if var in val:
            mapping[key] = val[var]

    return mapping


@lru_cache
def biosphere_flows_dictionary(version):
    """
    Create a dictionary with biosphere flows
    (name, category, sub-category, unit) -> code
    """
    if version == "3.9":
        fp = DATA_DIR / "utils" / "export" / "flows_biosphere_39.csv"
    else:
        fp = DATA_DIR / "utils" / "export" / "flows_biosphere_38.csv"

    if not Path(fp).is_file():
        raise FileNotFoundError("The dictionary of biosphere flows could not be found.")

    csv_dict = {}

    with open(fp, encoding="utf-8") as file:
        input_dict = csv.reader(
            file,
            delimiter=get_delimiter(filepath=fp),
        )
        for row in input_dict:
            csv_dict[(row[0], row[1], row[2], row[3])] = row[-1]

    return csv_dict


class InventorySet:
    """
    Hosts different filter sets to find equivalencies
    between ``premise`` terms and ``ecoinvent`` activities and exchanges.

    It stores:
    * material_filters: filters for activities related to materials.
    * powerplant_filters: filters for activities related to power generation technologies.
    * powerplant_fuel_filters: filters for fuel providers in power generation technologies.
    * fuel_filters: filters for fuel providers in general.
    * emissions_map: REMIND emission labels as keys, ecoinvent emission labels as values

    The functions :func:`generate_material_map`, :func:`generate_powerplant_map`
    and :func:`generate_fuel_map` can
    be used to extract the actual activity objects as dictionaries.
    These functions return the result of applying :func:`act_fltr` to the filter dictionaries.
    """

    def __init__(self, database: List[dict], version: str = None) -> None:
        self.database = database
        self.version = version

        self.powerplant_filters = get_mapping(
            filepath=POWERPLANT_TECHS, var="ecoinvent_aliases"
        )

        self.powerplant_fuels_filters = get_mapping(
            filepath=POWERPLANT_TECHS, var="ecoinvent_fuel_aliases"
        )

        self.fuels_filters = get_mapping(filepath=FUELS_TECHS, var="ecoinvent_aliases")

        self.materials_filters = get_mapping(
            filepath=MATERIALS_TECHS, var="ecoinvent_aliases"
        )

        self.daccs_filters = get_mapping(filepath=DAC_TECHS, var="ecoinvent_aliases")

        self.carbon_storage_filters = get_mapping(
            filepath=CARBON_STORAGE_TECHS, var="ecoinvent_aliases"
        )

        self.cement_fuel_filters = get_mapping(
            filepath=CEMENT_TECHS, var="ecoinvent_fuel_aliases"
        )

        self.gains_filters_EU = get_mapping(
            filepath=GAINS_MAPPING, var="ecoinvent_aliases"
        )

        self.activity_metals_filters = get_mapping(
            filepath=ACTIVITIES_METALS_MAPPING, var="ecoinvent_aliases"
        )
        self.metals_filters = get_mapping(
            filepath=METALS_MAPPING, var="ecoinvent_aliases"
        )

    def generate_gains_mapping_IAM(self, mapping):
        EU_to_IAM_var = get_mapping(filepath=GAINS_MAPPING, var="gains_aliases_IAM")
        new_map = defaultdict(set)
        for eu, iam in EU_to_IAM_var.items():
            new_map[iam].update(mapping[eu])

        return new_map

    def generate_gains_mapping(self):
        """
        Generate a dictionary with GAINS variables as keys and
        ecoinvent datasets as values.
        """
        return self.generate_sets_from_filters(self.gains_filters_EU)

    def generate_powerplant_map(self) -> dict:
        """
        Filter ecoinvent processes related to electricity production.

        :return: dictionary with el. prod. techs as keys (see below) and
            sets of related ecoinvent activities as values.
        :rtype: dict

        """
        return self.generate_sets_from_filters(self.powerplant_filters)

    def generate_daccs_map(self) -> dict:
        """
        Filter ecoinvent processes related to direct air capture.

        :return: dictionary with el. prod. techs as keys (see below) and
            sets of related ecoinvent activities as values.
        :rtype: dict

        """
        return self.generate_sets_from_filters(self.daccs_filters)

    def generate_carbon_storage_map(self) -> dict:
        """
        Filter ecoinvent processes related to carbon storage.

        :return: dictionary with el. prod. techs as keys (see below) and
            sets of related ecoinvent activities as values.
        :rtype: dict

        """
        return self.generate_sets_from_filters(self.carbon_storage_filters)

    def generate_powerplant_fuels_map(self) -> dict:
        """
        Filter ecoinvent processes related to electricity production.

        :return: dictionary with el. prod. techs as keys (see below) and
            sets of related ecoinvent activities as values.
        :rtype: dict

        """
        return self.generate_sets_from_filters(self.powerplant_fuels_filters)

    def generate_cement_fuels_map(self) -> dict:
        """
        Filter ecoinvent processes related to cement production.

        :return: dictionary with el. prod. techs as keys (see below) and
            sets of related ecoinvent activities as values.
        :rtype: dict

        """
        return self.generate_sets_from_filters(self.cement_fuel_filters)

    def generate_fuel_map(self) -> dict:
        """
        Filter ecoinvent processes related to fuel supply.

        :return: dictionary with fuel names as keys (see below) and
            sets of related ecoinvent activities as values.
        :rtype: dict

        """
        return self.generate_sets_from_filters(self.fuels_filters)

    def generate_material_map(self) -> dict:
        """
        Filter ecoinvent processes related to materials.
        Rerurns a dictionary with material names as keys (see below) and
        a set of related ecoinvent activities' names as values.
        """
        return self.generate_sets_from_filters(self.materials_filters)

    def generate_activities_using_metals_map(self) -> dict:
        """
        Filter ecoinvent processes related to metals.
        Returns a dictionary with metal names as keys (see below) and
        a set of related ecoinvent activities' names as values.
        """
        return self.generate_sets_from_filters(self.activity_metals_filters)

    def generate_metals_map(self) -> dict:
        """
        Filter ecoinvent processes related to metals.
        Returns a dictionary with metal names as keys (see below) and
        a set of related ecoinvent activities' names as values.
        """

        return self.generate_sets_from_filters(
            self.metals_filters,
            database=[
                {"name": k[0], "categories": k[1]}
                for k in biosphere_flows_dictionary(version=self.version)
            ],
        )

    @staticmethod
    def act_fltr(
        database: List[dict],
        fltr: Union[str, List[str]] = None,
        mask: Union[str, List[str]] = None,
        filter_exact: bool = False,
        mask_exact: bool = False,
    ) -> List[dict]:
        """Filter `database` for activities matching field contents given by `fltr` excluding strings in `mask`.
        `fltr`: string, list of strings or dictionary.
        If a string is provided, it is used to match the name field from the start (*startswith*).
        If a list is provided, all strings in the lists are used and results are joined (*or*).
        A dict can be given in the form <fieldname>: <str> to filter for <str> in <fieldname>.
        `mask`: used in the same way as `fltr`, but filters add up with each other (*and*).
        `filter_exact` and `mask_exact`: boolean, set `True` to only allow for exact matches.

        :param database: A lice cycle inventory database
        :type database: brightway2 database object
        :param fltr: value(s) to filter with.
        :type fltr: Union[str, lst, dict]
        :param mask: value(s) to filter with.
        :type mask: Union[str, lst, dict]
        :param filter_exact: requires exact match when true.
        :type filter_exact: bool
        :param mask_exact: requires exact match when true.
        :type mask_exact: bool
        :return: list of activity data set names
        :rtype: list

        """
        if fltr is None:
            fltr = {}
        if mask is None:
            mask = {}

        # default field is name
        if isinstance(fltr, (list, str)):
            fltr = {"name": fltr}
        if isinstance(mask, (list, str)):
            mask = {"name": mask}

        assert len(fltr) > 0, "Filter dict must not be empty."

        # find `act` in `database` that match `fltr`
        # and do not match `mask`
        filters = []
        for field, value in fltr.items():
            if isinstance(value, list):
                filters.extend([ws.either(*[ws.contains(field, v) for v in value])])
            else:
                filters.append(ws.contains(field, value))

        for field, value in mask.items():
            if isinstance(value, list):
                filters.extend([ws.exclude(ws.contains(field, v)) for v in value])
            else:
                filters.append(ws.exclude(ws.contains(field, value)))

        return list(ws.get_many(database, *filters))

    def generate_sets_from_filters(self, filtr: dict, database=None) -> dict:
        """
        Generate a dictionary with sets of activity names for
        technologies from the filter specifications.

            :param filtr:
            :func:`activity_maps.InventorySet.act_fltr`.
        :return: dictionary with the same keys as provided in filter
            and a set of activity data set names as values.
        :rtype: dict
        """

        database = database or self.database

        techs = {tech: self.act_fltr(database, **fltr) for tech, fltr in filtr.items()}
        return {tech: {act["name"] for act in actlst} for tech, actlst in techs.items()}
