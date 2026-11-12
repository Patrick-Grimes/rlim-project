"""
scripts/visualize_silver.py
Sprint 2 Silver Feature Store: Diagnostic & Exploratory Visualizations
"""

from pathlib import Path
import duckdb
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import seaborn as sns

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

def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # 1. Query Analytical Views
    # Panel 1: Buffer counts and total acreage
    df_buffers = con.execute("""
        SELECT 
            transit_buffer_tier,
            COUNT(*) AS parcel_count,
            ROUND(SUM(calc_acreage), 1) AS total_acres,
            ROUND(AVG(dist_to_station_meters), 1) AS mean_dist_m
        FROM silver.parcels_spatial
        GROUP BY transit_buffer_tier
        ORDER BY mean_dist_m ASC;
    """).df()

    # Panel 2: Zoning composition
    df_zoning = con.execute("""
        SELECT 
            transit_buffer_tier,
            zoning_category,
            COUNT(*) AS count
        FROM silver.parcels_spatial
        GROUP BY transit_buffer_tier, zoning_category;
    """).df()

    # Panel 3: Hedonic Sample (Sampled to 15k rows to prevent memory crashes)
    df_hedonic = con.execute("""
        SELECT 
            p.parcel_id,
            p.dist_to_station_meters,
            p.dist_to_cbd_ft / 5280.0 AS dist_to_cbd_miles,
            p.transit_buffer_tier,
            p.zoning_category,
            s.nominal_price,
            s.real_price_2026,
            s.ppsf_real_2026,
            LN(s.ppsf_real_2026) AS log_real_ppsf,
            s.sale_year
        FROM silver.parcels_spatial p
        INNER JOIN silver.sales_standardized s 
            ON p.parcel_id = s.parcel_id
        WHERE s.ppsf_real_2026 >= 0.50 
          AND s.nominal_price >= 10000
        USING SAMPLE 15000;
    """).df()

    # Panel 4: Nominal vs Real CPI 2026
    df_trends = con.execute("""
        SELECT 
            sale_year,
            ROUND(MEDIAN(nominal_price), 0) AS median_nominal,
            ROUND(MEDIAN(real_price_2026), 0) AS median_real_2026,
            COUNT(*) AS transaction_count
        FROM silver.sales_standardized
        WHERE nominal_price >= 10000
        GROUP BY sale_year
        ORDER BY sale_year ASC;
    """).df()

    con.close()

    # 2. Render Analytical Dashboard
    fig, axes = plt.subplots(2, 2, figsize=(15, 11))
    fig.suptitle("Red Line Impact Model (RLIM) — Silver Layer Analytical Summary", fontweight="bold", y=0.98)

    # Panel 1: Walkshed Land Distribution
    ax1 = axes[0, 0]
    tier_order = ["QUARTER_MILE_WALK", "HALF_MILE_WALK", "ONE_MILE_ACCESS", "OUTSIDE_WALKSHED"]
    colors = ["#2b5c8f", "#4682b4", "#7fb3d5", "#d5dbdb"]
    tier_color_map = dict(zip(tier_order, colors))

    palette = [tier_color_map.get(t, "#95a5a6") for t in df_buffers["transit_buffer_tier"]]

    bars = ax1.bar(
        df_buffers["transit_buffer_tier"],
        df_buffers["parcel_count"],
        color=palette,
        edgecolor="#333333",
        linewidth=0.8,
    )
    ax1.set_title("Parcel Distribution by Transit Catchment Tier", fontweight="bold")
    ax1.set_ylabel("Total Tax Parcels")
    ax1.tick_params(axis="x", rotation=15)

    for bar in bars:
        height = bar.get_height()
        ax1.annotate(
            f"{height:,}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontweight="bold",
        )

    # Panel 2: Zoning Breakdown
    ax2 = axes[0, 1]
    if not df_zoning.empty:
        pivot_zoning = df_zoning.pivot(
            index="transit_buffer_tier", columns="zoning_category", values="count"
        ).fillna(0)
        # Reindex tiers to match urban hierarchy
        present_tiers = [t for t in tier_order if t in pivot_zoning.index]
        pivot_zoning = pivot_zoning.reindex(present_tiers)
        pivot_zoning.plot(
            kind="bar",
            stacked=True,
            ax=ax2,
            colormap="Blues",
            edgecolor="#333333",
            linewidth=0.5,
        )
        ax2.set_title("Zoning Category Composition across Buffer Tiers", fontweight="bold")
        ax2.set_ylabel("Parcel Count")
        ax2.tick_params(axis="x", rotation=15)
        ax2.legend(title="Harmonized Zoning", frameon=True)

    # Panel 3: Spatial Distance Gradient (Semi-Log Hedonic Fit)
    ax3 = axes[1, 0]
    if not df_hedonic.empty:
        sns.scatterplot(
            data=df_hedonic,
            x="dist_to_station_meters",
            y="log_real_ppsf",
            hue="transit_buffer_tier",
            alpha=0.45,
            ax=ax3,
            s=22,
        )
        # Proper semi-log regression: regressing log_real_ppsf linearly on distance
        sns.regplot(
            data=df_hedonic,
            x="dist_to_station_meters",
            y="log_real_ppsf",
            scatter=False,
            ax=ax3,
            color="#c0392b",
            line_kws={"linestyle": "--", "linewidth": 2.2, "label": "Semi-Log Hedonic Fit"},
        )
        ax3.set_title("Hedonic Land Value Gradient vs. Station Proximity", fontweight="bold")
        ax3.set_xlabel("Euclidean Distance to Nearest Platform (Meters)")
        ax3.set_ylabel("Natural Log Real PPSF (ln(2026 USD / SqFt))")
        ax3.legend(frameon=True, loc="upper right")

    # Panel 4: Deflation Impact (Nominal vs. Real 2026 Dollars)
    ax4 = axes[1, 1]
    if not df_trends.empty:
        ax4.plot(
            df_trends["sale_year"],
            df_trends["median_real_2026"],
            marker="o",
            linewidth=2.2,
            color="#27ae60",
            label="Real (2026 BLS Deflated)",
        )
        ax4.plot(
            df_trends["sale_year"],
            df_trends["median_nominal"],
            marker="s",
            linewidth=2.0,
            linestyle=":",
            color="#7f8c8d",
            label="Nominal Recorded",
        )
        ax4.set_title("Macro Adjustment: Nominal vs. Real Sales Price", fontweight="bold")
        ax4.set_xlabel("Transaction Closing Year")
        ax4.set_ylabel("Median Deed Price (USD)")
        ax4.yaxis.set_major_formatter(ticker.StrMethodFormatter("${x:,.0f}"))
        ax4.legend(frameon=True)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    out_file = OUTPUT_DIR / "silver_feature_store_summary.png"
    plt.savefig(out_file, dpi=300)
    plt.close()
    print(f"✓ Silver summary figure saved to: {out_file}")

if __name__ == "__main__":
    main()
