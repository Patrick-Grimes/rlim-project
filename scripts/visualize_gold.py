"""
scripts/visualize_gold.py
Sprint 3 Gold Modeling Layer: Econometric & Causal Diagnostic Dashboard
"""

from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

# Set publication style
plt.style.use("seaborn-v0_8-whitegrid")
plt.rcParams.update(
    {
        "font.family": "sans-serif",
        "font.size": 10,
        "axes.labelsize": 11,
        "axes.titlesize": 12,
        "figure.titlesize": 14,
    }
)

DB_PATH = Path("data/transit_warehouse.duckdb")
OUTPUT_DIR = Path("docs/figures")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

con = duckdb.connect(str(DB_PATH), read_only=True)

print("Querying gold.hedonic_sample for econometric visualizations...")

# -------------------------------------------------------------------------
# 1. Query Analytical DataFrames
# -------------------------------------------------------------------------

# A. DiD Trends: Treated (BLE Walkshed) vs Control (Matthews Walkshed) across 2012-2026
df_did_trends = con.execute("""
    SELECT 
        sale_year,
        CASE WHEN nearest_line = 'BLE' THEN 'Treated: BLE Walkshed (<= 0.5 mi)'
             ELSE 'Control: Matthews Corridor (<= 0.5 mi)'
        END AS cohort,
        ROUND(MEDIAN(ppsf_real_2026), 2) AS median_real_ppsf,
        ROUND(AVG(ppsf_real_2026), 2) AS mean_real_ppsf,
        COUNT(*) AS transaction_count
    FROM gold.hedonic_sample
    WHERE nearest_line IN ('BLE', 'Control_Matthews') AND in_walkshed = 1
    GROUP BY sale_year, cohort
    ORDER BY sale_year ASC, cohort;
""").df()

# B. Cross-Corridor Land Premium Distribution
df_corridor_box = con.execute("""
    SELECT 
        nearest_line AS corridor,
        ppsf_real_2026,
        transit_buffer_tier
    FROM gold.hedonic_sample
    WHERE ppsf_real_2026 BETWEEN 5.0 AND 120.0
    USING SAMPLE 25000;
""").df()

# C. Hedonic Gradient Shift: Pre-Opening (2012-2017) vs Active Revenue Service (2018-2026)
df_gradient = con.execute("""
    SELECT 
        dist_to_station_meters,
        ppsf_real_2026,
        CASE WHEN era_revenue_service = 1 THEN 'Revenue Service (2018-2026)'
             ELSE 'Pre-Opening & Construction (2012-2017)'
        END AS transit_era
    FROM gold.hedonic_sample
    WHERE nearest_line IN ('BL', 'BLE') 
      AND dist_to_station_meters <= 3000
      AND ppsf_real_2026 BETWEEN 2.0 AND 150.0
    USING SAMPLE 15000;
""").df()

# D. Monocentric CBD Decay by Zoning Classification
df_cbd_zoning = con.execute("""
    SELECT 
        dist_to_cbd_miles,
        ppsf_real_2026,
        zoning_category
    FROM gold.hedonic_sample
    WHERE dist_to_cbd_miles <= 25.0
      AND ppsf_real_2026 BETWEEN 2.0 AND 150.0
    USING SAMPLE 15000;
""").df()

con.close()
print("  ✓ Data queries complete. Generating dashboard...")

# -------------------------------------------------------------------------
# 2. Render 2x2 Gold Econometric Dashboard
# -------------------------------------------------------------------------

fig, axes = plt.subplots(2, 2, figsize=(16, 12))
fig.suptitle(
    "Red Line Impact Model (RLIM) — Gold Econometric & Causal Modeling Summary",
    fontweight="bold",
    y=0.98,
)

# -------------------------------------------------------------------------
# Panel 1: Quasi-Experimental DiD Trends (Treated vs Control)
# -------------------------------------------------------------------------
ax1 = axes[0, 0]
if not df_did_trends.empty:
    for cohort, color, marker in [
        ("Treated: BLE Walkshed (<= 0.5 mi)", "#1f77b4", "o"),
        ("Control: Matthews Corridor (<= 0.5 mi)", "#7f8c8d", "s"),
    ]:
        sub = df_did_trends[df_did_trends["cohort"] == cohort]
        ax1.plot(
            sub["sale_year"],
            sub["median_real_ppsf"],
            marker=marker,
            linewidth=2.5,
            color=color,
            label=cohort,
        )

    # Shaded Event Eras
    ax1.axvline(2017.5, color="#d9534f", linestyle="--", linewidth=1.8, label="BLE Opening (March 2018)")
    ax1.axvspan(2014, 2017.5, color="#f0ad4e", alpha=0.15, label="Anticipation / Construction")

    ax1.set_title("Difference-in-Differences: Parallel Trends & Operational Shock", fontweight="bold")
    ax1.set_xlabel("Transaction Closing Year")
    ax1.set_ylabel("Median Real PPSF (2026 USD / SqFt)")
    ax1.yaxis.set_major_formatter("${x:,.2f}")
    ax1.legend(loc="upper left", frameon=True, fontsize=9)
else:
    ax1.text(0.5, 0.5, "No DiD Data Available", ha="center", va="center", transform=ax1.transAxes)

# -------------------------------------------------------------------------
# Panel 2: Land Value Distribution Across Study Corridors
# -------------------------------------------------------------------------
ax2 = axes[0, 1]
corridor_order = ["BL", "BLE", "Red Line", "Control_Matthews"]
palette = ["#2b5c8f", "#4682b4", "#e67e22", "#95a5a6"]

sns.boxplot(
    data=df_corridor_box,
    x="corridor",
    y="ppsf_real_2026",
    order=corridor_order,
    palette=palette,
    ax=ax2,
    fliersize=1.2,
    linewidth=1.0,
)
ax2.set_title("Real Land Value Distributions by Regional Corridor", fontweight="bold")
ax2.set_xlabel("Corridor Alignment")
ax2.set_ylabel("Real PPSF (2026 USD / SqFt)")
ax2.set_xticklabels(["Blue Line (South)", "BLE (Northeast)", "Red Line (North)", "Matthews (Control)"])
ax2.yaxis.set_major_formatter("${x:,.0f}")

# -------------------------------------------------------------------------
# Panel 3: Hedonic Distance Decay: Pre-Opening vs. Operational
# -------------------------------------------------------------------------
ax3 = axes[1, 0]
era_colors = {"Pre-Opening & Construction (2012-2017)": "#7f8c8d", "Revenue Service (2018-2026)": "#27ae60"}

for era, col in era_colors.items():
    sub = df_gradient[df_gradient["transit_era"] == era]
    sns.regplot(
        data=sub,
        x="dist_to_station_meters",
        y="ppsf_real_2026",
        scatter_kws={"alpha": 0.15, "s": 10, "color": col},
        line_kws={"linewidth": 2.2, "label": f"Fit: {era}", "color": col},
        ax=ax3,
    )

ax3.set_yscale("log")
ax3.set_title("Hedonic Station Distance Gradient: Pre vs. Post Opening", fontweight="bold")
ax3.set_xlabel("Distance to Nearest Station Platform (Meters)")
ax3.set_ylabel("Log Real PPSF (2026 USD / SqFt)")
ax3.legend(loc="upper right", frameon=True, fontsize=9)

# -------------------------------------------------------------------------
# Panel 4: Monocentric CBD Gradient by Zoning Classification
# -------------------------------------------------------------------------
ax4 = axes[1, 1]
zoning_palette = {"TOD": "#e74c3c", "MFR": "#3498db", "OTHER_COMMERCIAL": "#f39c12", "SFR": "#2ecc71"}

sns.scatterplot(
    data=df_cbd_zoning,
    x="dist_to_cbd_miles",
    y="ppsf_real_2026",
    hue="zoning_category",
    palette=zoning_palette,
    alpha=0.35,
    s=18,
    ax=ax4,
)
# Overall monocentric OLS trend
sns.regplot(
    data=df_cbd_zoning,
    x="dist_to_cbd_miles",
    y="ppsf_real_2026",
    scatter=False,
    color="#2c3e50",
    line_kws={"linestyle": "--", "linewidth": 2.0, "label": "CBD Monocentric Gradient"},
    ax=ax4,
)

ax4.set_yscale("log")
ax4.set_title("Monocentric Rent Gradient: Distance to Trade & Tryon", fontweight="bold")
ax4.set_xlabel("Distance to Charlotte CBD (Miles)")
ax4.set_ylabel("Log Real PPSF (2026 USD / SqFt)")
ax4.legend(loc="upper right", frameon=True, fontsize=9)

plt.tight_layout(rect=[0, 0.03, 1, 0.95])

out_file = OUTPUT_DIR / "gold_modeling_summary.png"
plt.savefig(out_file, dpi=300)
plt.close()

print(f"✓ Gold dashboard figure saved to: {out_file}")

if __name__ == "__main__":
    pass
