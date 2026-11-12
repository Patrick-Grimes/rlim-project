"""
scripts/models_hedonic.py
Estimates hedonic gradient, quasi-experimental DiD, and event-study specifications.
"""

from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

DB_PATH = "data/transit_warehouse.duckdb"
OUTPUT_DIR = Path("docs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def main():
    print("=" * 75)
    print("RLIM: HEDONIC PRICING & QUASI-EXPERIMENTAL DID ESTIMATION")
    print("=" * 75)

    con = duckdb.connect(DB_PATH, read_only=True)
    print("\nExtracting modeling panel from gold.hedonic_sample...")
    df = con.execute("""
        SELECT 
            sale_id,
            parcel_id,
            sale_year,
            log_real_ppsf,
            log_real_price,
            real_price_2026,
            nominal_price,
            ppsf_real_2026,
            shape_area_sqft,
            calc_acreage,
            dist_to_cbd_miles,
            dist_to_station_meters,
            dist_to_station_ft,
            transit_buffer_tier,
            zoning_category,
            nearest_line,
            nearest_station_name,
            in_walkshed,
            is_blue_line,
            is_control_corridor,
            is_red_line,
            era_pre_construction,
            era_anticipation,
            era_revenue_service
        FROM gold.hedonic_sample
        WHERE nominal_price >= 10000 
          AND ppsf_real_2026 >= 0.50;
    """).df()
    con.close()
    print(f"  ✓ Loaded {len(df):,} arms-length transactions.")

    df['dist_station_km'] = df['dist_to_station_meters'] / 1000.0

    # 1. Model 1: Intra-Corridor Hedonic Gradient
    print("\n--- Estimating Model 1: Intra-Corridor Hedonic Gradient ---")
    ols_formula = (
        "log_real_ppsf ~ dist_station_km + dist_to_cbd_miles + "
        "C(nearest_line) + C(zoning_category, Treatment(reference='SFR')) + C(sale_year)"
    )
    ols_model = smf.ols(ols_formula, data=df).fit(
        cov_type="cluster", 
        cov_kwds={'groups': df['nearest_station_name']}
    )
    print(ols_model.summary().tables[1])

    # 2. Model 2: Difference-in-Differences
    print("\n--- Estimating Model 2: Difference-in-Differences ---")
    df_did = df[
        (df['nearest_line'].isin(['BLE', 'Control_Matthews'])) & 
        (df['in_walkshed'] == 1)
    ].copy()

    df_did['treated'] = (df_did['nearest_line'] == 'BLE').astype(int)
    df_did['post'] = (df_did['sale_year'] >= 2018).astype(int)
    df_did['treated_post'] = df_did['treated'] * df_did['post']

    did_formula = (
        "log_real_ppsf ~ treated + treated_post + dist_to_cbd_miles + "
        "C(zoning_category, Treatment(reference='SFR')) + C(sale_year)"
    )
    did_model = smf.ols(did_formula, data=df_did).fit(
        cov_type="cluster", 
        cov_kwds={'groups': df_did['nearest_station_name']}
    )
    print(did_model.summary().tables[1])

    # 3. Model 3: Event-Study Specification
    print("\n--- Estimating Model 3: Event-Study Specification ---")
    event_formula = (
        "log_real_ppsf ~ treated + C(sale_year, Treatment(reference=2017)) + "
        "treated:C(sale_year, Treatment(reference=2017)) + "
        "dist_to_cbd_miles + C(zoning_category, Treatment(reference='SFR'))"
    )
    event_model = smf.ols(event_formula, data=df_did).fit(
        cov_type="cluster", 
        cov_kwds={'groups': df_did['nearest_station_name']}
    )

    years = sorted(df_did['sale_year'].unique())
    coefs, ci_lower, ci_upper = [], [], []

    for yr in years:
        if yr == 2017:
            coefs.append(0.0)
            ci_lower.append(0.0)
            ci_upper.append(0.0)
        else:
            key1 = f"treated:C(sale_year, Treatment(reference=2017))[T.{yr}]"
            key2 = f"C(sale_year, Treatment(reference=2017))[T.{yr}]:treated"
            param_key = key1 if key1 in event_model.params else key2
            b = event_model.params.get(param_key, 0.0)
            se = event_model.bse.get(param_key, 0.0)
            coefs.append(b)
            ci_lower.append(b - 1.96 * se)
            ci_upper.append(b + 1.96 * se)

    # 4. Render Event-Study Figure
    print("\nRendering Event-Study Parallel Trends Figure...")
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(11, 6))

    ax.plot(years, coefs, color="#1f77b4", marker="o", linewidth=2.2, label=r"DiD Impact $\delta_\tau$ (Station-Clustered 95% CI)")
    ax.fill_between(years, ci_lower, ci_upper, color="#1f77b4", alpha=0.18)

    ax.axhline(0, color="#333333", linestyle="-", linewidth=0.9, alpha=0.7)
    ax.axvline(2017.5, color="#d9534f", linestyle="--", linewidth=1.8, label="BLE Revenue Service (March 2018)")
    ax.axvspan(2014, 2017.5, color="#f0ad4e", alpha=0.12, label="Funding & Construction Window")

    ax.set_title("Event-Study: Land Capitalization Along LYNX BLE vs. Matthews Control", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Transaction Year", fontsize=11, labelpad=8)
    ax.set_ylabel("Treatment Differential: ln(Real PPSF)", fontsize=11, labelpad=8)
    ax.set_xticks(years)
    ax.tick_params(axis="x", rotation=0)
    ax.legend(loc="upper left", frameon=True)

    fig_path = OUTPUT_DIR / "did_parallel_trends.png"
    plt.tight_layout()
    plt.savefig(fig_path, dpi=300)
    plt.close()
    print(f"  ✓ Figure saved to: {fig_path}")

    # 5. Export Summary
    table_path = Path("docs/regression_summary.md")
    did_treat_post = did_model.params['treated_post']
    did_se = did_model.bse['treated_post']
    did_pval = did_model.pvalues['treated_post']
    did_pct = (np.exp(did_treat_post) - 1.0) * 100.0

    b_dist = ols_model.params.get('dist_station_km', 0.0)
    se_dist = ols_model.bse.get('dist_station_km', 0.0)
    pval_dist = ols_model.pvalues.get('dist_station_km', 0.0)
    decay_pct = (np.exp(b_dist) - 1.0) * 100.0

    md_content = f"""# RLIM Econometric Output: Hedonic Baseline & Difference-in-Differences

## Model Summary Table (Station-Clustered Standard Errors)

| Model | Specification | Dependent Variable | Primary Estimator | Coef | Station-Clustered SE | p-value | Implied % Impact |
|---|---|---|---|---|---|---|---|
| **Model 1** | Intra-Corridor Hedonic OLS | $\\ln(\\text{{Real PPSF}})$ | Distance to Station (per km) | `{b_dist:.4f}` | `{se_dist:.4f}` | `{pval_dist:.4e}` | `{decay_pct:.2f}%` per km |
| **Model 2** | Quasi-Experimental DiD | $\\ln(\\text{{Real PPSF}})$ | Treated $\\times$ Post (2018+) | `{did_treat_post:.4f}` | `{did_se:.4f}` | `{did_pval:.4e}` | `{did_pct:+.2f}%` premium |

### Methodological Summary
* **Intra-Corridor Variation:** Corridor fixed effects (`C(nearest_line)`) prevent baseline regional differences from biasing the station distance decay parameter.
* **Macroeconomic Controls:** Calendar year fixed effects (`C(sale_year)`) absorb regional post-2018 economic fluctuations.
* **Clustered Inference:** Standard errors are clustered at the station platform level (`nearest_station_name`) to account for localized spatial correlation.
"""
    table_path.write_text(md_content)
    print(f"  ✓ Markdown table generated at: {table_path}")

if __name__ == "__main__":
    main()
