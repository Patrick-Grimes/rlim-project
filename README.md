# Red Line Impact Model (RLIM)

Empirical analysis of transit-oriented land capitalization, market liquidity, and displacement risk across Mecklenburg County rail corridors. This repository evaluates the proposed 25-mile CATS Red Line commuter rail alignment (Uptown Charlotte to Mooresville) using the LYNX Blue Line Extension (BLE) and the unbuilt Matthews/Silver Line corridor as a quasi-experimental benchmark.

**Interactive Documentation & Results:** Open `docs/index.html` (or view via GitHub Pages) for the full data dictionary, interactive Plotly figures, regression tables, and methodological notes.

---

## Repository Structure

```text
rlim-project/
├── data-dict.yaml                # Warehouse schema spec (v0.4.0)
├── data/
│   └── transit_warehouse.duckdb  # DuckDB spatial database (gitignored)
├── docs/
│   ├── index.html                # Compiled standalone HTML dashboard
│   ├── regression_summary.md     # Hedonic OLS & DiD estimates
│   ├── liquidity_summary.md      # Poisson GLM turnover estimates
│   └── figures/                  # Diagnostic plots & event-study figures
└── scripts/
    ├── fetch_bronze.py           # Ingests parcels, stations & deed sales
    ├── transform_silver.py       # Reprojects EPSG:2264, distances & CPI
    ├── build_gold.py             # Materializes Gold analytical views
    ├── models_hedonic.py         # Estimates hedonic, DiD & event study
    ├── models_liquidity.py       # Estimates Poisson turnover rate models
    ├── visualize_silver.py       # Renders Silver spatial & CPI charts
    ├── visualize_gold.py         # Renders Gold capitalization & churn charts
    ├── build_docs.py             # Compiles data-dict & tables to index.html
    └── validate_pipeline.py      # Runs automated warehouse & file audits
```

---

## Data Architecture (`data-dict.yaml`)

The pipeline runs on a three-tier DuckDB spatial warehouse (`EPSG:2264`, NC State Plane):

* **Bronze (`bronze.parcels`, `bronze.gtfs_stops`, `bronze.property_sales`):** Raw Mecklenburg County cadastral boundaries, CATS station coordinates, and Register of Deeds transaction records ingested via `fetch_bronze.py`.
* **Silver (`silver.stations_spatial`, `silver.parcels_spatial`, `silver.cpi_deflator`, `silver.sales_standardized`):** Built by `transform_silver.py`. Standardizes zoning categories (`SFR`, `MFR`, `OTHER_COMMERCIAL`, `TOD`), computes Euclidean distances to station platforms and Trade & Tryon (Charlotte CBD), assigns pedestrian catchment tiers (`<= 0.25 mi`, `<= 0.50 mi`, `<= 1.00 mi`), and deflates arm's-length deed prices ($\ge \$10{,}000$) to real 2026 dollars via BLS CPI-U.
* **Gold (`gold.hedonic_sample`, `gold.transaction_velocity`):** Built by `build_gold.py`. Modeling views joining deflated transactions with spatial covariates, policy era indicators (`2012–2013` baseline, `2014–2017` construction/anticipation, `2018–2026` revenue service), and a balanced stratum-year panel tracking annual turnover rates per 100 parcels.

---

## Econometric Specifications

1. **Continuous Hedonic Gradient (`scripts/models_hedonic.py`):** Semi-log OLS regressing $\ln(\text{Real PPSF}_{\text{Land}})$ on station platform distance ($\text{km}$), distance to Charlotte CBD ($\text{miles}$), corridor fixed effects, zoning fixed effects, and calendar-year fixed effects, with standard errors clustered by station platform.
2. **Walkshed Difference-in-Differences & Event Study (`scripts/models_hedonic.py`):** Compares walkable catchments ($\le 0.5\text{ mi}$) along the treated LYNX Blue Line Extension against the unbuilt Matthews/Silver Line counterfactual, anchored at reference year $\tau = 2017$.
3. **Market Liquidity & Turnover Velocity (`scripts/models_liquidity.py`):** Poisson Generalized Linear Models with log parcel-stock exposure offsets ($\ln(\text{total\_parcels})$) and stratum-clustered standard errors testing whether commercial and TOD parcels turn over faster than residential stock prior to revenue service.

---

## Reproduction Workflow

Run the pipeline sequentially from the repository root to rebuild the warehouse from scratch and regenerate all artifacts:

```bash
# 1. Ingest raw data and build Silver spatial feature store
python scripts/fetch_bronze.py
python scripts/transform_silver.py

# 2. Materialize Gold analytical views
python scripts/build_gold.py

# 3. Run econometric estimations and export summary tables
python scripts/models_hedonic.py
python scripts/models_liquidity.py

# 4. Render figures and compile the HTML documentation
python scripts/visualize_silver.py
python scripts/visualize_gold.py
python scripts/build_docs.py

# 5. Run automated validation checks
python scripts/validate_pipeline.py
```
