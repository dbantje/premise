Mining sensitivity tests
========================

These optional tests implement five literature-grounded changes to **prepared
cut-off inventories**. They are conditional technology cases, not forecasts of
global adoption in 2050. Ordinary ``NewDatabase.update()`` behavior is unchanged.

The numerical performance assumptions are versioned in
``premise/data/mining/sensitivity_sources.json``. Each result records that source,
the complete target configuration, before/after amounts, derived parameters,
created suppliers and limitations. No change is made to the baseline inventory.

Performance assumptions and boundaries
-------------------------------------

``grinding_best_practice``
    Reduces the **allocated grinding electricity**, not total mine electricity,
    by 15%. The US DOE (2007), Appendix B Exhibit 24 and Appendix D, compares
    57,900 with 49,200 Btu/ton for metal-mining grinding. This is an engineering
    benchmark, not a statistical uncertainty range. Supply a documented
    ``stage_share`` for each electricity exchange.

``hpgr``
    Reduces **allocated grinding-machine energy** by 62%, from McIvor et al.
    (2023), Table 8, comparing plant ball milling with pilot HPGR. The wider
    reported circuit savings include auxiliaries and embodied grinding-media
    energy. They are deliberately not applied to all electricity inputs. Supply
    auxiliary, equipment and media changes separately when available. This
    implementation otherwise reports the narrower machine-energy boundary.

``haulage_electrification``
    Uses the published diesel/electric efficiencies of 35%/90% from Aydogdu et
    al. (2024), giving ``0.35 / 0.90 / 3.6 = 0.10802469 kWh/MJ`` of baseline
    diesel input for equal useful work. This is a transfer of their conveyor
    work-equivalence assumption to the explicitly allocated haulage duty.

    Only ``diesel, burned in building machine`` services in MJ are supported.
    A single-provider unit-weight service market can be traversed. The service
    and its market path are copied for the selected mine: shared suppliers used
    elsewhere are unchanged. Raw diesel and direct combustion-service air
    emissions are removed from the copy, electricity is added, and existing
    machinery, lubrication and non-air releases are retained as proxies.
    Explicitly document why the service's air releases are combustion emissions.
    This avoids giving free machinery-production credits, but it is not a full
    electric-conveyor/fleet LCI. New equipment and infrastructure can be supplied
    using ``additional_exchanges``. Raw diesel in kg, diesel generators and
    independently recorded mine emissions are not automatically converted.

``drainage_treatment``
    Uses the paired mean removal efficiencies from Hustwit and Sykes (1994),
    Table 2, full-scale Penn Mine operational periods:

    =========== ========= ========= ========
    Performance Copper    Zinc      Cadmium
    =========== ========= ========= ========
    second      0.981     0.958     0.913
    third       0.995     0.991     0.981
    =========== ========= ========= ========

    These are alternative observed operating states, not confidence limits.
    Select aqueous ``Copper ion``, ``Zinc II`` and ``Cadmium II`` flows by exact
    compartment. For a collected fraction ``c``, new removal efficiency ``eta``
    and documented pre-existing removal ``eta0``, the exchange multiplier is::

        (1 - c) + c * (1 - eta) / (1 - eta0)

    ``eta0`` defaults to zero, meaning additional treatment of the selected
    released load, not a claim that no other site controls exist. Document the
    collected fraction **and treatment duration**. Long-term water compartments
    are rejected: operational measurements do not justify extrapolation over
    ecoinvent's long-term release horizon. Natural-resource uptake is untouched.

    ``scope="emission_potential"`` is an explicitly optimistic release-potential
    calculation without treatment burdens. ``scope="with_treatment_inventory"``
    requires additional exchanges and their source. Include the appropriate
    water-volume-dependent lime, electricity and sludge-management burdens;
    their completeness remains the analyst's responsibility.

``copper_recovery``
    Implements Sun et al. (2025)'s coarse-tailings flotation performance at
    Dexing: 55% (``low``) or 65% (``high``) recovery from coarse tailings. The
    coarse fraction is 37% of tailings mass and contains 68% of lost copper.
    These Dexing-specific fractions need an explicit transfer justification.
    Their product has only 0.25--0.40% Cu and needs further beneficiation.

    Supply documented baseline mine recovery ``r0`` and downstream recovery
    ``rd``. For a constant reference-concentrate grade and fixed allocation::

        captured_fraction_of_Cu_loss = 0.68 * (0.55 or 0.65) * rd
        additional_concentrate = (1 - r0) / r0 * captured_fraction_of_Cu_loss
        throughput_multiplier = 1 / (1 + additional_concentrate)

    The entire original mining/beneficiation inventory is normalized by the
    increased output. Additional concentrate mass is subtracted from the
    selected tailings stream before normalization. Recovery inputs must be
    specified **per kg of coarse tailings processed**, including downstream
    beneficiation and finishing losses. Residual tailings already return to the
    existing treatment path in this implementation: do **not** add their full
    disposal inventory a second time. If using a published foreground process
    that includes residue disposal, remove the duplicated baseline disposal or
    supply the net change in routing. Keep dry/wet mass bases consistent. Inputs
    are multiplied by ``original_tailings_mass * 0.37 * throughput_multiplier``.

    The target must be the kg copper sulfide-concentrate mining/beneficiation
    activity; finished-copper or integrated smelting activities are rejected.
    Existing economic allocation and other coproduct markets are held fixed.
    This is a sensitivity in allocated inventories, not a new multi-output mine
    allocation model.

    ``tailings_model="constant_leachable_fraction"`` is an explicit modeling
    assumption: per original ore throughput, other aqueous loads are conserved
    and Cu loads decrease with removed Cu. The treatment path is copied for this
    mine. Per-kg release factors increase for other substances because residual
    tailings mass decreases. All water compartments, including long-term, are
    adjusted by this **material-content** calculation; no long-term drainage
    treatment efficiency is applied. Changes in mineralogy, pH and leachability
    are not predicted. This first-order approximation assumes recovered gangue
    carries no other modeled contaminants; it needs reassessment for a complete
    mineralogical treatment model.

    ``scope="yield_potential"`` omits recovery burdens and reports that limitation.
    ``scope="with_recovery_inventory"`` requires sourced additional inputs.
    Do not interpret the former as a net environmental improvement of the
    actual recovery technology.

Grounding the target allocations
-------------------------------

The five performance assumptions do not identify which fraction of an ecoinvent
electricity input runs a mill, how much machine service is haulage, which drainage
is captured, or a mine's baseline recovery. Those require inventory documentation,
process models or appropriate site literature. The API rejects missing numerical
allocations and missing evidence. It does not infer these quantities from voltage
levels, a generic dataset name or elemental resource flows in allocated datasets.

The configuration template in ``examples/mining_sensitivities.template.json``
uses **null** for these missing values. It is deliberately not executable until
calibrated. It covers all five cases using ecoinvent 3.10.1 Chilean copper dataset
names. Update exact suppliers and compartments for the actual prepared scenario;
premise may have regionalized or replaced them.

Apply the same technology case to reference and intervention scenarios. Keep
the baseline separate. The engine rejects repeated/stacked sensitivities because
grinding/HPGR savings and recovery improvements can overlap. A combined case
needs an explicitly reconciled flowsheet rather than multiplying these factors.

Usage with NewDatabase
----------------------

Build the ordinary scenario first, then fork it::

    import json
    from pathlib import Path
    from premise import NewDatabase

    ndb = NewDatabase(...)  # existing paper settings and encrypted IAM inputs
    ndb.update()
    config = json.loads(Path("calibrated_mining_cases.json").read_text())
    for entry in config["cases"]:
        variant = ndb.with_mining_sensitivity(**entry)
        for i, report in enumerate(variant.mining_sensitivity_reports):
            Path(f"{entry['label']}-{i}-report.json").write_text(
                json.dumps(report, indent=2)
            )
        variant.write_db_to_brightway()

Default Brightway database names include the sensitivity label. For a
multi-scenario build, ``targets`` can instead be a callable accepting a scenario
dictionary and returning region/year-specific targets. The baseline is retained
in memory to survive the exporters' ordinary cache cleanup. Sector updates must
be completed before forking. Each fork contains independent inventories and
cleared transformation indices/caches. IAM pathway/year metadata stays intact.

The source inventory also stays available through the standalone API::

    from premise.mining_sensitivity import apply_mining_sensitivity

    result = apply_mining_sensitivity(
        prepared_wurst_database,
        case="grinding_best_practice",
        targets=calibrated_targets,
        label="S1-grinding",
    )
    result.write_report("S1-grinding-report.json")
    changed_database = result.database

Activity selectors require exact ``name``, ``reference product`` and ``location``.
Technosphere exchange selectors require exact ``name``, ``product``, ``location``
and ``unit``. Biosphere selectors require exact ``name``, ``categories`` and
``unit``. Zero or multiple matches fail. This prevents silent regional fallbacks.
Supplementary exchanges use exact supplier selectors and signed amounts; keep
waste-treatment signs consistent with the source cut-off inventory.

Existing exchange uncertainties are rescaled with their amounts. New process
inputs and published performance factors are deterministic case assumptions;
the published cases do not establish probability distributions. Zeroed exchanges
lose stale uncertainty parameters. Changed suppliers lose stale internal links
and are relinked on export; unmodified external/biosphere links remain intact.

Audit and run an existing Brightway database
-------------------------------------------

The CLI first inventories candidate energy, waste and Cu/Zn/Cd exchanges::

    python dev/run_mining_sensitivities.py \
      --project ecoinvent-3.10.1-cutoff \
      --database ecoinvent-3.10.1-cutoff \
      --output /tmp/mining-audit

After calibrating targets::

    python dev/run_mining_sensitivities.py \
      --project ecoinvent-3.10.1-cutoff \
      --database ecoinvent-3.10.1-cutoff \
      --config calibrated_mining_cases.json \
      --output /tmp/mining-cases

This writes audit/provenance reports and independent Wurst pickles. Add
``--write-brightway`` to write new uniquely labeled databases. Existing Brightway
destinations and case-output files are never overwritten. All case configurations
are validated before the first variant is written. A runtime failure during
publication can still leave earlier outputs; use the manifest/reports to inspect
what completed. The CLI targets Brightway 2.5, as used by this branch.

The read-only integration check can be reproduced against the local licensed
database with::

    python dev/validate_mining_sensitivities.py \
      --project ecoinvent-3.10.1-cutoff \
      --database ecoinvent-3.10.1-cutoff \
      --output /tmp/mining-software-validation.json

This deliberately uses synthetic stage/collection/recovery calibration values
to exercise the code. It verifies all seven configurations (five test types),
including semantic supplier links and source-inventory immutability. It writes
no modified database or inventory pickle and produces no LCIA results. Its
calibration values must not be reused as research assumptions.

Inventory pickles contain licensed ecoinvent data: keep them locally and do not
commit or redistribute them. The audit is not a calibrated scenario or an LCIA
result. Use the paper's original functional units, demand vectors and LCIA methods
to assess the resulting variants; inspect both total impacts and the reference
versus intervention difference.

References
----------

* US DOE (2007). `U.S. Mining Industry Energy Bandwidth Study
  <https://www.energy.gov/sites/default/files/2013/11/f4/mining_bandwidth.pdf>`_.
* McIvor et al. (2023). `Development of Fine High-Pressure Grinding for Mineral
  Processing Plants
  <https://www.ceecthefuture.org/resources?file=publication_file&id=4114&task=download>`_.
* Aydogdu et al. (2024). `Mining haulage electrification
  <https://doi.org/10.3390/su16146232>`_.
* Hustwit and Sykes (1994). `Pipeline Treatment of a Metal Mine Drainage
  Containing Copper and Zinc
  <https://stacks.cdc.gov/view/cdc/227561/cdc_227561_DS1.pdf>`_.
* Sun et al. (2025). `Industrial coarse copper-tailings recovery
  <https://doi.org/10.1016/j.mineng.2025.109695>`_.
