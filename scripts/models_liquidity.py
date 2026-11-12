"""
scripts/models_liquidity.py
Estimates Poisson GLM rate models of transaction velocity and turnover divergence.
"""

from pathlib import Path
import duckdb
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

DB_PATH = "data/transit_warehouse.duckdb"
OUTPUT_DIR = Path("docs")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def main():
    print("=" * 75)
    print("RLIM: TRANSACTION VELOCITY & LIQUIDITY RATE MODELS")
    print("=" * 75)

    con = duckdb.connect(DB_PATH, read_only=True)
    df = con.execute("""
        SELECT 
            nearest_line,
            in_walkshed,
            zoning_category,
            sale_year,
            tx_count,
            total_parcels,
            turnover_rate_pct,
            policy_era
        FROM gold.transaction_velocity
        WHERE total_parcels > 0;
    """).df()
    con.close()

    df_did = df[
        (df['nearest_line'].isin(['BLE', 'Control_Matthews'])) & 
        (df['in_walkshed'] == 1)
    ].copy()

    df_did['treated'] = (df_did['nearest_line'] == 'BLE').astype(int)
    df_did['is_commercial'] = (df_did['zoning_category'].isin(['OTHER_COMMERCIAL', 'TOD'])).astype(int)
    df_did['era_anticipation'] = (df_did['policy_era'] == 'Anticipation / Construction').astype(int)
    df_did['era_revenue'] = (df_did['policy_era'] == 'Revenue Service').astype(int)
    df_did['stratum_id'] = df_did['nearest_line'] + "_" + df_did['zoning_category']

    # 1. Model L1: Poisson Rate Model of Sales Velocity
    print("\n--- Model L1: Poisson Rate Model of Sales Velocity ---")
    f_poisson = (
        "tx_count ~ treated + era_anticipation + era_revenue + "
        "treated:era_anticipation + treated:era_revenue + "
        "C(zoning_category, Treatment(reference='SFR'))"
    )
    poisson_mod = smf.glm(
        formula=f_poisson,
        data=df_did,
        offset=np.log(df_did['total_parcels']),
        family=sm.families.Poisson()
    ).fit(cov_type="HC1")
    
    phi = poisson_mod.pearson_chi2 / poisson_mod.df_resid
    print(f"Dispersion Parameter (Phi = chi2 / df): {phi:.3f}")
    
    poisson_clust = smf.glm(
        formula=f_poisson,
        data=df_did,
        offset=np.log(df_did['total_parcels']),
        family=sm.families.Poisson()
    ).fit(cov_type="cluster", cov_kwds={'groups': df_did['stratum_id']})

    print("\nModel L1 Estimates (Stratum-Clustered SEs):")
    print(poisson_clust.summary().tables[1])

    # 2. Model L2: Triple-Difference Specification (Commercial vs. Residential)
    print("\n--- Model L2: Commercial vs. Residential Divergence (DDD) ---")
    f_triple = (
        "tx_count ~ treated + is_commercial + era_anticipation + era_revenue + "
        "treated:is_commercial + treated:era_anticipation + treated:era_revenue + "
        "is_commercial:era_anticipation + is_commercial:era_revenue + "
        "treated:era_anticipation:is_commercial + treated:era_revenue:is_commercial"
    )
    triple_mod = smf.glm(
        formula=f_triple,
        data=df_did,
        offset=np.log(df_did['total_parcels']),
        family=sm.families.Poisson()
    ).fit(cov_type="cluster", cov_kwds={'groups': df_did['stratum_id']})
    print(triple_mod.summary().tables[1])

    # 3. Export Summary
    beta_ant = poisson_clust.params.get('treated:era_anticipation', 0.0)
    se_ant = poisson_clust.bse.get('treated:era_anticipation', 0.0)
    pval_ant = poisson_clust.pvalues.get('treated:era_anticipation', 0.0)

    beta_rev = poisson_clust.params.get('treated:era_revenue', 0.0)
    se_rev = poisson_clust.bse.get('treated:era_revenue', 0.0)
    pval_rev = poisson_clust.pvalues.get('treated:era_revenue', 0.0)

    beta_ddd = triple_mod.params.get('treated:era_anticipation:is_commercial', 0.0)
    se_ddd = triple_mod.bse.get('treated:era_anticipation:is_commercial', 0.0)
    pval_ddd = triple_mod.pvalues.get('treated:era_anticipation:is_commercial', 0.0)

    md_content = f"""# RLIM Econometric Output: Market Liquidity & Velocity Shocks

## Poisson Rate Models of Transaction Turnover

Dependent Variable: Transaction Count $Q_{{c,z,t}}$ with exposure offset $\\ln(\\text{{Total Parcels}}_{{c,z}})$.
Sample: Pedestrian walkshed ($\le 0.5\\text{{ mi}}$) across BLE and Matthews Control.
Dispersion scale parameter: $\\hat{{\\phi}} = {phi:.3f}$.

| Estimator / Parameter | Description | Coef ($\\beta$) | Clustered SE | p-value | Relative Rate Change ($e^\\beta - 1$) |
|---|---|---|---|---|---|
| **Treated $\\times$ Anticipation** (Model L1) | Walkshed turnover surge (2014–2017) | `{beta_ant:.4f}` | `{se_ant:.4f}` | `{pval_ant:.4e}` | `{(np.exp(beta_ant) - 1.0) * 100:+.2f}%` |
| **Treated $\\times$ Revenue Service** (Model L1) | Walkshed turnover surge (2018+) | `{beta_rev:.4f}` | `{se_rev:.4f}` | `{pval_rev:.4e}` | `{(np.exp(beta_rev) - 1.0) * 100:+.2f}%` |
| **Treated $\\times$ Anticipation $\\times$ Comm** (Model L2 DDD) | Differential commercial churn acceleration | `{beta_ddd:.4f}` | `{se_ddd:.4f}` | `{pval_ddd:.4e}` | `{(np.exp(beta_ddd) - 1.0) * 100:+.2f}%` |

### Methodological Summary
1. **Balanced Panel Preservation:** Strata with zero transactions in a calendar year are explicitly modeled with $Q=0$ rather than omitted.
2. **Stratum-Clustered Standard Errors:** Clustered standard errors account for within-unit serial correlation across the 15-year panel.
3. **Turnover Velocity Dynamics:** Sales turnover rate ($Q/N$) captures parcel assembly activity and market liquidity shifts that occur before price equilibrium clears.
"""
    out_table = OUTPUT_DIR / "liquidity_summary.md"
    out_table.write_text(md_content)
    print(f"\n✓ Output exported to: {out_table}")

if __name__ == "__main__":
    main()
