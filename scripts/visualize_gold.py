"""
scripts/visualize_gold.py
Sprint 3 Gold Modeling Layer: Econometric Diagnostics (Price & Liquidity)
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

    print("=" * 75)
    print("RLIM SPRINT 3: GOLD LAYER VISUALIZATION SUITE")
    print("=" * 75)

    # =========================================================================
    # PART 1: PRICE CAPITALIZATION & HEDONICS (gold_modeling_summary.png)
    # =========================================================================
    print("\n[1/2] Querying gold.hedonic_sample for Price & Causal Models...")

    # A. DiD Trends: Treated (BLE Walkshed) vs Control (Matthews Walkshed)
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

    # C. Hedonic Gradient Shift: Pre-Opening vs Revenue Service
    df_gradient = con.execute("""
        SELECT 
            dist_to_station_meters,
            ppsf_real_2026,
            LN(ppsf_real_2026) AS log_real_ppsf,
            CASE WHEN era_revenue_service = 1 THEN 'Revenue Service (2018-2026)'
                 ELSE 'Pre-Opening & Construction (2012-2017)'
            END AS transit_era
        FROM gold.hedonic_sample
        WHERE nearest_line IN ('BL', 'BLE') 
          AND dist_to_station_meters <= 3000
          AND ppsf_real_2026 BETWEEN 2.0 AND 150.0
        USING SAMPLE 15000;
    """).df()

    # D. Monocentric CBD Decay by Zoning
    df_cbd_zoning = con.execute("""
        SELECT 
            dist_to_cbd_miles,
            ppsf_real_2026,
            LN(ppsf_real_2026) AS log_real_ppsf,
            zoning_category
        FROM gold.hedonic_sample
        WHERE dist_to_cbd_miles <= 25.0
          AND ppsf_real_2026 BETWEEN 2.0 AND 150.0
        USING SAMPLE 15000;
    """).df()

    print("  Rendering Price Dashboard (2x2 Grid)...")
    fig1, axes1 = plt.subplots(2, 2, figsize=(16, 12))
    fig1.suptitle("Red Line Impact Model (RLIM) — Gold Econometric & Causal Modeling Summary", fontweight="bold", y=0.98)

    # Panel 1: DiD Parallel Trends
    ax1 = axes1[0, 0]
    if not df_did_trends.empty:
        for cohort, color, marker in [
            ("Treated: BLE Walkshed (<= 0.5 mi)", "#1f77b4", "o"),
            ("Control: Matthews Corridor (<= 0.5 mi)", "#7f8c8d", "s"),
        ]:
            sub = df_did_trends[df_did_trends["cohort"] == cohort]
            ax1.plot(sub["sale_year"], sub["median_real_ppsf"], marker=marker, linewidth=2.5, color=color, label=cohort)

        ax1.axvline(2017.5, color="#d9534f", linestyle="--", linewidth=1.8, label="BLE Opening (March 2018)")
        ax1.axvspan(2014, 2017.5, color="#f0ad4e", alpha=0.15, label="Anticipation / Construction")
        ax1.set_title("Difference-in-Differences: Parallel Trends & Operational Shock", fontweight="bold")
        ax1.set_xlabel("Transaction Closing Year")
        ax1.set_ylabel("Median Real PPSF (2026 USD / SqFt)")
        ax1.yaxis.set_major_formatter(ticker.StrMethodFormatter("${x:,.2f}"))
        ax1.legend(loc="upper left", frameon=True, fontsize=9)

    # Panel 2: Corridor Boxplots (Fixed Indentation & Updated 25-Mile Label)
    ax2 = axes1[0, 1]
    corridor_order = ["BL", "BLE", "Red Line", "Control_Matthews"]
    palette = ["#2b5c8f", "#4682b4", "#e67e22", "#95a5a6"]
    sns.boxplot(
        data=df_corridor_box,
        x="corridor",
        y="ppsf_real_2026",
        hue="corridor",
        order=corridor_order,
        palette=palette,
        ax=ax2,
        fliersize=1.2,
        linewidth=1.0,
        legend=False,
    )
    ax2.set_title("Real Land Value Distributions by Regional Corridor", fontweight="bold")
    ax2.set_xlabel("Corridor Alignment")
    ax2.set_ylabel("Real PPSF (2026 USD / SqFt)")
    ax2.set_xticks(range(len(corridor_order)))
    # Fixed indentation and refined label for entire 25-mile corridor
    ax2.set_xticklabels(["Blue Line (South)", "BLE (Northeast)", "Red Line (Entire Corridor)", "Matthews (Control)"])
    ax2.yaxis.set_major_formatter(ticker.StrMethodFormatter("${x:,.0f}"))

    # Panel 3: Semi-Log Hedonic Decay by Era
    ax3 = axes1[1, 0]
    era_colors = {"Pre-Opening & Construction (2012-2017)": "#7f8c8d", "Revenue Service (2018-2026)": "#27ae60"}
    for era, col in era_colors.items():
        sub = df_gradient[df_gradient["transit_era"] == era]
        sns.regplot(
            data=sub,
            x="dist_to_station_meters",
            y="log_real_ppsf",
            scatter_kws={"alpha": 0.15, "s": 10, "color": col},
            line_kws={"linewidth": 2.2, "label": f"Fit: {era}", "color": col},
            ax=ax3,
        )
    ax3.set_title("Semi-Log Station Distance Gradient: Pre vs. Post Opening", fontweight="bold")
    ax3.set_xlabel("Distance to Nearest Station Platform (Meters)")
    ax3.set_ylabel("Natural Log Real PPSF (ln(2026 USD / SqFt))")
    ax3.legend(loc="upper right", frameon=True, fontsize=9)

    # Panel 4: Monocentric CBD Gradient
    ax4 = axes1[1, 1]
    zoning_palette = {"TOD": "#e74c3c", "MFR": "#3498db", "OTHER_COMMERCIAL": "#f39c12", "SFR": "#2ecc71"}
    sns.scatterplot(
        data=df_cbd_zoning,
        x="dist_to_cbd_miles",
        y="log_real_ppsf",
        hue="zoning_category",
        palette=zoning_palette,
        alpha=0.35,
        s=18,
        ax=ax4,
    )
    sns.regplot(
        data=df_cbd_zoning,
        x="dist_to_cbd_miles",
        y="log_real_ppsf",
        scatter=False,
        color="#2c3e50",
        line_kws={"linestyle": "--", "linewidth": 2.0, "label": "CBD Monocentric Gradient"},
        ax=ax4,
    )
    ax4.set_title("Monocentric Rent Gradient: Distance to Trade & Tryon", fontweight="bold")
    ax4.set_xlabel("Distance to Charlotte CBD (Miles)")
    ax4.set_ylabel("Natural Log Real PPSF (ln(2026 USD / SqFt))")
    ax4.legend(loc="upper right", frameon=True, fontsize=9)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    out_file1 = OUTPUT_DIR / "gold_modeling_summary.png"
    plt.savefig(out_file1, dpi=300)
    plt.close()
    print(f"  ✓ Saved: {out_file1}")

    # =========================================================================
    # PART 2: LIQUIDITY & TRANSACTION VELOCITY (liquidity_velocity_shocks.png)
    # =========================================================================
    print("\n[2/2] Querying gold.transaction_velocity for Liquidity & Churn...")

    has_velocity = con.execute("""
        SELECT count(*) FROM information_schema.tables 
        WHERE table_schema = 'gold' AND table_name = 'transaction_velocity';
    """).fetchone()[0]

    if has_velocity > 0:
        df_corridor_churn = con.execute("""
            SELECT 
                sale_year,
                nearest_line,
                ROUND(SUM(tx_count)::DOUBLE / NULLIF(SUM(total_parcels), 0) * 100.0, 2) AS annual_churn_pct
            FROM gold.transaction_velocity
            WHERE nearest_line IN ('BLE', 'Control_Matthews', 'Red Line') AND in_walkshed = 1
            GROUP BY sale_year, nearest_line
            ORDER BY sale_year ASC;
        """).df()

        df_zoning_churn = con.execute("""
            SELECT 
                sale_year,
                CASE 
                    WHEN zoning_category IN ('OTHER_COMMERCIAL', 'TOD') THEN 'Commercial / Retail'
                    ELSE 'Residential (SFR / MFR)'
                END AS broad_zoning,
                ROUND(SUM(tx_count)::DOUBLE / NULLIF(SUM(total_parcels), 0) * 100.0, 2) AS annual_churn_pct
            FROM gold.transaction_velocity
            WHERE nearest_line = 'BLE' AND in_walkshed = 1
            GROUP BY 
                sale_year,
                CASE 
                    WHEN zoning_category IN ('OTHER_COMMERCIAL', 'TOD') THEN 'Commercial / Retail'
                    ELSE 'Residential (SFR / MFR)'
                END
            ORDER BY sale_year ASC;
        """).df()

        fig2, axes2 = plt.subplots(1, 2, figsize=(15, 6))
        fig2.suptitle("Market Liquidity & Transaction Velocity: Announcement & Construction Shocks", fontweight="bold", y=0.98)

        # Panel 1: Churn across Treated, Control, and Red Line
        ax_c1 = axes2[0]
        p_map1 = {'BLE': '#1f77b4', 'Control_Matthews': '#7f8c8d', 'Red Line': '#e67e22'}
        l_map1 = {'BLE': 'Treated: BLE Walkshed', 'Control_Matthews': 'Control: Matthews Corridor', 'Red Line': 'Planned: Red Line (Full Corridor)'}
        for line, sub in df_corridor_churn.groupby('nearest_line'):
            ax_c1.plot(sub['sale_year'], sub['annual_churn_pct'], marker='o', linewidth=2.2, color=p_map1.get(line, '#333333'), label=l_map1.get(line, line))
        ax_c1.axvline(2012.8, color="#2ca02c", linestyle=":", linewidth=1.8, label="FTA FFGA Grant (Late 2012)")
        ax_c1.axvspan(2014, 2017.5, color="#f0ad4e", alpha=0.15, label="Active Construction Window")
        ax_c1.axvline(2017.5, color="#d9534f", linestyle="--", linewidth=1.8, label="Revenue Opening (March 2018)")
        ax_c1.set_title("Walkshed Turnover Rate: Treated vs. Counterfactuals", fontweight="bold")
        ax_c1.set_xlabel("Transaction Closing Year")
        ax_c1.set_ylabel("Turnover Rate (Sales per 100 Parcels)")
        ax_c1.legend(loc="upper left", frameon=True, fontsize=8.5)

        # Panel 2: Commercial vs Residential Churn
        ax_c2 = axes2[1]
        p_map2 = {'Commercial / Retail': '#d9534f', 'Residential (SFR / MFR)': '#2b5c8f'}
        for cat, sub in df_zoning_churn.groupby('broad_zoning'):
            ax_c2.plot(sub['sale_year'], sub['annual_churn_pct'], marker='s', linewidth=2.4, color=p_map2.get(cat, '#333333'), label=cat)
        ax_c2.axvline(2012.8, color="#2ca02c", linestyle=":", linewidth=1.8, label="Grant Announcement")
        ax_c2.axvspan(2014, 2017.5, color="#f0ad4e", alpha=0.15, label="Construction Window")
        ax_c2.axvline(2017.5, color="#d9534f", linestyle="--", linewidth=1.8, label="Revenue Opening")
        ax_c2.set_title("BLE Walkshed Churn: Commercial vs. Residential", fontweight="bold")
        ax_c2.set_xlabel("Transaction Closing Year")
        ax_c2.set_ylabel("Turnover Rate (Sales per 100 Parcels)")
        ax_c2.legend(loc="upper left", frameon=True, fontsize=8.5)

        plt.tight_layout(rect=[0, 0.03, 1, 0.95])
        out_file2 = OUTPUT_DIR / "liquidity_velocity_shocks.png"
        plt.savefig(out_file2, dpi=300)
        plt.close()
        print(f"  ✓ Saved: {out_file2}")
    else:
        print("  ⚠️ gold.transaction_velocity view not found in warehouse. Run scripts/build_gold.py first.")

    con.close()
    print("\n" + "=" * 75)
    print("🎯 ALL GOLD FIGURES GENERATED.")
    print("=" * 75)

if __name__ == "__main__":
    main()
