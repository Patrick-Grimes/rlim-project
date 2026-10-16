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
    print("RLIM SPRINT 3: HEDONIC PRICING & QUASI-EXPERIMENTAL DID ESTIMATION")
    print("=" * 75)

    con = duckdb.connect(DB_PATH, read_only=True)

    # 1. Pull analytical panel from Gold layer
    print("\nExtracting modeling panel from gold.hedonic_sample...")
    df = con.execute("""
        SELECT 
            sale_id,
            parcel_id,
            sale_year,
            log_real_ppsf,
            log_real_price,
            real_price_2026,
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
        FROM gold.hedonic_sample;
    """).df()
    con.close()
    print(f"  ✓ Loaded {len(df):,} transactions for estimation.")

    # -------------------------------------------------------------
    # MODEL 1: BASELINE HEDONIC OLS (Pooled Corridor Cross-Section)
    # Specification: ln(PP_sqft) = b0 + b1*dist_station_km + b2*dist_cbd + Controls + Year_FE
    # -------------------------------------------------------------
    print("\n--- Estimating Model 1: Baseline Hedonic OLS (Proximity Gradient) ---")
    df['dist_station_km'] = df['dist_to_station_meters'] / 1000.0
    
    ols_formula = (
        "log_real_ppsf ~ dist_station_km + dist_to_cbd_miles + "
        "C(zoning_category, Treatment(reference='SFR')) + C(sale_year)"
    )
    ols_model = smf.ols(ols_formula, data=df).fit(cov_type="HC1")
    print(ols_model.summary().tables[1])

    # -------------------------------------------------------------
    # MODEL 2: DIFFERENCE-IN-DIFFERENCES (BLE vs. Matthews Control)
    # Treated: BLE Walkshed (<= 0.5 mi)
    # Control: Matthews / Independence unbuilt corridor (<= 0.5 mi)
    # Pre-Period: 2012-2017 (Pre-Opening & Construction)
    # Post-Period: 2018-2026 (Active Revenue Service)
    # -------------------------------------------------------------
    print("\n--- Estimating Model 2: Quasi-Experimental DiD (BLE vs. Matthews) ---")
    df_did = df[
        (df['nearest_line'].isin(['BLE', 'Control_Matthews'])) & 
        (df['in_walkshed'] == 1)
    ].copy()

    df_did['treated'] = (df_did['nearest_line'] == 'BLE').astype(int)
    df_did['post'] = (df_did['sale_year'] >= 2018).astype(int)
    df_did['treated_post'] = df_did['treated'] * df_did['post']

    print(f"  DiD Subsample: {len(df_did):,} sales across Treated (BLE) and Control (Matthews) walksheds.")
    did_formula = (
        "log_real_ppsf ~ treated + post + treated_post + dist_to_cbd_miles + "
        "C(zoning_category, Treatment(reference='SFR'))"
    )
    did_model = smf.ols(did_formula, data=df_did).fit(cov_type="HC1")
    print(did_model.summary().tables[1])

    # -------------------------------------------------------------
    # MODEL 3: EVENT-STUDY SPECIFICATION (Testing Parallel Trends)
    # Reference Year = 2017 (Last full calendar year prior to March 2018 revenue opening)
    # -------------------------------------------------------------
    print("\n--- Estimating Model 3: Event-Study Specification ---")
    event_formula = (
        "log_real_ppsf ~ treated + C(sale_year, Treatment(reference=2017)) + "
        "treated:C(sale_year, Treatment(reference=2017)) + "
        "dist_to_cbd_miles + C(zoning_category, Treatment(reference='SFR'))"
    )
    event_model = smf.ols(event_formula, data=df_did).fit(cov_type="HC1")

    # Extract interaction coefficients and 95% CIs
    years = sorted(df_did['sale_year'].unique())
    coefs = []
    ci_lower = []
    ci_upper = []

    for yr in years:
        if yr == 2017:
            coefs.append(0.0)
            ci_lower.append(0.0)
            ci_upper.append(0.0)
        else:
            param_key = f"treated:C(sale_year, Treatment(reference=2017))[T.{yr}]"
            b = event_model.params.get(param_key, np.nan)
            se = event_model.bse.get(param_key, np.nan)
            coefs.append(b)
            ci_lower.append(b - 1.96 * se)
            ci_upper.append(b + 1.96 * se)

    # -------------------------------------------------------------
    # 4. RENDER PUBLICATION-QUALITY PARALLEL TRENDS PLOT
    # -------------------------------------------------------------
    print("\nRendering Event-Study Parallel Trends Figure...")
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(11, 6))

    ax.plot(years, coefs, color="#1f77b4", marker="o", linewidth=2.2, label=r"DiD Impact $\beta_\tau$")
    ax.fill_between(years, ci_lower, ci_upper, color="#1f77b4", alpha=0.18, label="95% Robust CI")

    # Benchmarks
    ax.axhline(0, color="#333333", linestyle="-", linewidth=0.9, alpha=0.7)
    ax.axvline(2017.5, color="#d9534f", linestyle="--", linewidth=1.8, label="BLE Opening (March 2018)")
    ax.axvspan(2014, 2017.5, color="#f0ad4e", alpha=0.12, label="Active Construction Window")

    ax.set_title("Event-Study: Capitalization Shock Along LYNX BLE vs. Matthews Control", fontsize=13, fontweight="bold", pad=12)
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

    # -------------------------------------------------------------
    # 5. WRITE FORMAL ECONOMETRIC SUMMARY TABLE (Markdown Artifact)
    # -------------------------------------------------------------
    table_path = Path("docs/regression_summary.md")
    did_treat_post = did_model.params['treated_post']
    did_se = did_model.bse['treated_post']
    did_pval = did_model.pvalues['treated_post']
    did_pct = (np.exp(did_treat_post) - 1.0) * 100.0

    md_content = f"""# RLIM Econometric Output: Hedonic Baseline & Difference-in-Differences

## Model Summary Table

| Model | Specification | Dependent Variable | Primary Estimator | Coef | Std. Err (HC1) | p-value | Implied % Impact |
|---|---|---|---|---|---|---|---|
| **Model 1** | Baseline Pooled Hedonic OLS | $\\ln(\\text{{Real PPSF}})$ | Distance to Station (per km) | `{ols_model.params.get('dist_station_km', 0):.4f}` | `{ols_model.bse.get('dist_station_km', 0):.4f}` | `{ols_model.pvalues.get('dist_station_km', 0):.4e}` | `{((np.exp(ols_model.params.get('dist_station_km', 0)) - 1) * 100):.2f}%` per km |
| **Model 2** | Quasi-Experimental DiD | $\\ln(\\text{{Real PPSF}})$ | Treated $\\times$ Post (2018+) | `{did_treat_post:.4f}` | `{did_se:.4f}` | `{did_pval:.4e}` | `{did_pct:+.2f}%` premium |

### Interpretation
* **Baseline Hedonic Gradient:** Evaluates spatial distance decay across all 4 study corridors.
* **Quasi-Experimental Shock (DiD):** Isolates the operational transit premium of the LYNX Blue Line Extension relative to the unbuilt Matthews / Independence control corridor, controlling for macro trends and zoning composition.
* **Event Study (Parallel Trends):** Pre-2018 coefficients indicate whether treated and control walksheds moved along parallel trajectories prior to revenue service opening.
"""
    table_path.write_text(md_content)
    print(f"  ✓ Markdown table generated at: {table_path}")

    print("\n" + "=" * 75)
    print("🎯 SPRINT 3 ECONOMETRIC MODELS ESTIMATED SUCCESSFULLY.")
    print("=" * 75)

if __name__ == "__main__":
    main()
