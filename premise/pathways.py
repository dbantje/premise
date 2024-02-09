import json
import shutil
from datetime import date
from pathlib import Path
from typing import List

import xarray as xr
import yaml
from datapackage import Package

from . import __version__
from .activity_maps import act_fltr
from .energy import Energy
from .new_database import NewDatabase


class PathwaysDataPackage:
    def __init__(
        self,
        scenarios: List[dict],
        years: List[int] = range(2005, 2105, 5),
        source_version: str = "3.9",
        source_type: str = "brightway",
        key: bytes = None,
        source_db: str = None,
        source_file_path: str = None,
        additional_inventories: List[dict] = None,
        system_model: str = "cutoff",
        system_args: dict = None,
        external_scenarios: list = None,
        gains_scenario="CLE",
        use_absolute_efficiency=False,
    ):
        self.years = years
        self.scenarios = []
        for year in years:
            for scenario in scenarios:
                new_entry = scenario.copy()
                new_entry["year"] = year
                self.scenarios.append(new_entry)

        self.source_db = source_db
        self.source_version = source_version
        self.key = key

        self.datapackage = NewDatabase(
            scenarios=self.scenarios,
            source_version=source_version,
            source_type=source_type,
            key=key,
            source_db=source_db,
            source_file_path=source_file_path,
            additional_inventories=additional_inventories,
            system_model=system_model,
            system_args=system_args,
            external_scenarios=external_scenarios,
            gains_scenario=gains_scenario,
            use_absolute_efficiency=use_absolute_efficiency,
        )

    def create_datapackage(
        self, name: str = f"pathways_{date.today()}", contributors: list = None
    ):
        self.datapackage.update_all()

        for scenario in self.datapackage.scenarios:
            energy = Energy(
                database=scenario["database"],
                iam_data=scenario["iam data"],
                model=scenario["model"],
                pathway=scenario["pathway"],
                year=scenario["year"],
                version=self.datapackage.version,
                system_model=self.datapackage.system_model,
            )
            energy.import_heating_inventories()
            scenario["database"] = energy.database

        self.export_datapackage(name)

    def export_datapackage(self, name: str, contributors: list = None):
        # first, delete the content of the "pathways" folder
        shutil.rmtree(Path.cwd() / "pathways", ignore_errors=True)
        # create matrices in current directory
        self.datapackage.write_db_to_matrices(
            filepath=str(Path.cwd() / "pathways" / "inventories")
        )
        self.add_scenario_data()
        self.add_variables_mapping()
        self.build_datapackage(name, contributors)

    def find_activities(self, filters: [str, list], mask: [str, list, None], database):
        """
        Find activities in the database.

        :param filters: value(s) to filter with.
        :type filters: Union[str, lst, dict]
        :param mask: value(s) to filter with.
        :type mask: Union[str, lst, dict]
        :param database: A lice cycle inventory database
        :type database: brightway2 database object
        :return: list dictionaries with activity names, reference products and units
        """
        return [
            {
                "name": act["name"],
                "reference product": act["reference product"],
                "unit": act["unit"],
            }
            for act in act_fltr(
                database=database,
                fltr=filters,
                mask=mask,
            )
        ]

    def add_variables_mapping(self):
        """
        Add variables mapping in the "pathways" folder.

        """

        # create a "mapping" folder inside "pathways"
        (Path.cwd() / "pathways" / "mapping").mkdir(parents=True, exist_ok=True)

        # make a list of unique variables
        vars = [
            self.datapackage.scenarios[s]["iam data"]
            .data.coords["variables"]
            .values.tolist()
            for s in range(len(self.scenarios))
        ]
        # remove efficiency and emissions variables
        vars = [
            [
                v
                for v in var
                if "efficiency" not in v.lower() and "emission" not in v.lower()
            ]
            for var in vars
        ]
        # concatenate the list
        vars = list(set([item for sublist in vars for item in sublist]))

        mapping = {}

        # iterate through all YAML files contained in the "iam_variables_mapping" folder
        # the folder is located in the same folder as this module

        model_variables = []

        for file in (
            Path(__file__).resolve().parent.glob("iam_variables_mapping/*.yaml")
        ):
            # open the file
            with open(file, "r") as f:
                # load the YAML file
                data = yaml.full_load(f)
            # iterate through all variables in the YAML file
            for var, val in data.items():
                if all(x in val for x in ["iam_aliases", "ecoinvent_aliases"]):
                    for model, model_var in val["iam_aliases"].items():
                        if model_var in vars and model in [
                            s["model"] for s in self.scenarios
                        ]:
                            if model_var not in model_variables:
                                model_variables.append(model_var)
                                mapping[var] = {"scenario variable": model_var}
                                mapping[var]["dataset"] = self.find_activities(
                                    filters=val["ecoinvent_aliases"].get("fltr"),
                                    mask=val["ecoinvent_aliases"].get("mask"),
                                    database=self.datapackage.scenarios[0]["database"],
                                )
                            else:
                                print(f"Leaving out {model_var} from {var}")

        # under each key, remove duplicates from list
        # to only keep unique name, reference product and unit
        for key, val in mapping.items():
            if "dataset" in val:
                mapping[key]["dataset"] = [
                    dict(t)
                    for t in {tuple(sorted(d.items())) for d in mapping[key]["dataset"]}
                ]

        with open(Path.cwd() / "pathways" / "mapping" / "mapping.yaml", "w") as f:
            yaml.dump(mapping, f)

    def add_scenario_data(self):
        """
        Add scenario data in the "pathways" folder.

        """
        # concatenate xarray across scenarios
        array = xr.concat(
            [
                self.datapackage.scenarios[s]["iam data"].data
                for s in range(len(self.scenarios))
            ],
            dim="scenario",
        )
        # add scenario data to the xarray
        array.coords["scenario"] = [
            f"{s['model'].upper()} - {s['pathway']}" for s in self.scenarios
        ]
        # make sure pathways/scenario_data directory exists
        (Path.cwd() / "pathways" / "scenario_data").mkdir(parents=True, exist_ok=True)
        # save the xarray as csv
        df = array.to_dataframe().reset_index()

        # add a unit column
        # units are contained as an attribute of the xarray
        df["unit"] = df["variables"].map(array.attrs["unit"])

        # split the columns "scenarios" into "model" and "pathway"
        df[["model", "pathway"]] = df["scenario"].str.split(" - ", n=1, expand=True)
        df = df.drop(columns=["scenario"])

        # if scenario_data file already exists, delete it
        if (Path.cwd() / "pathways" / "scenario_data" / "scenario_data.csv").exists():
            (Path.cwd() / "pathways" / "scenario_data" / "scenario_data.csv").unlink()

        df.to_csv(
            Path.cwd() / "pathways" / "scenario_data" / "scenario_data.csv", index=False
        )

    def build_datapackage(self, name: str, contributors: list = None):
        """
        Create and export a scenario datapackage.
        """
        # create a new datapackage
        package = Package(base_path=Path.cwd().as_posix())
        package.infer("pathways/**/*.csv")
        package.infer("pathways/**/*.yaml")

        package.descriptor["name"] = name.replace(" ", "_").lower()
        package.descriptor["title"] = name.capitalize()
        package.descriptor["description"] = (
            f"Data package generated by premise {__version__}."
        )
        package.descriptor["premise version"] = str(__version__)
        package.descriptor["scenarios"] = [
            {
                "name": f"{s['model'].upper()} - {s['pathway']}",
                "description": f"Prospective db, "
                f"based on {s['model'].upper()}, "
                f"pathway {s['pathway']}.",
            }
            for s in self.scenarios
        ]
        package.descriptor["keywords"] = [
            "ecoinvent",
            "scenario",
            "data package",
            "premise",
            "pathways",
        ]
        package.descriptor["licenses"] = [
            {
                "id": "CC0-1.0",
                "title": "CC0 1.0",
                "url": "https://creativecommons.org/publicdomain/zero/1.0/",
            }
        ]

        if contributors is None:
            contributors = [
                {
                    "title": "undefined",
                    "name": "anonymous",
                    "email": "anonymous@anonymous.com",
                }
            ]
        else:
            contributors = [
                {
                    "title": c.get("title", "undefined"),
                    "name": c.get("name", "anonymous"),
                    "email": c.get("email", "anonymous@anonymous.com"),
                }
                for c in contributors
            ]
        package.descriptor["contributors"] = contributors
        package.commit()

        # save the json file
        package.save(str(Path.cwd() / "pathways" / "datapackage.json"))

        # open the json file and ensure that all resource names are slugified
        with open(Path.cwd() / "pathways" / "datapackage.json", "r") as f:
            data = yaml.full_load(f)

        for resource in data["resources"]:
            resource["name"] = resource["name"].replace(" ", "_").lower()

        # also, remove"pathways/" from the path of each resource
        for resource in data["resources"]:
            resource["path"] = resource["path"].replace("pathways/", "")

        # save it back as a json file
        with open(Path.cwd() / "pathways" / "datapackage.json", "w") as fp:
            json.dump(data, fp)

        # zip the folder
        shutil.make_archive(name, "zip", str(Path.cwd() / "pathways"))

        print(f"Data package saved at {str(Path.cwd() / 'pathways' / f'{name}.zip')}")
