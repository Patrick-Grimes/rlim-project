"""
scripts/validate_pipeline.py
Automated validation audit verifying spatial platforms, parcel integrity,
longitudinal sales coverage, Gold analytical views, and compiled artifacts.
"""

import sys
from pathlib import Path
import duckdb

DB_PATH = "data/transit_warehouse.duckdb"
HTML_PATH = Path("docs/index.html")
FIG_DIR = Path("docs/figures")
MD_LIQUIDITY = Path("docs/liquidity_summary.md")
MD_REGRESSION = Path("docs/regression_summary.md")

def run_checks():
    print("=" * 75)
    print("RLIM: WAREHOUSE, MODELING & ARTIFACT VALIDATION AUDIT")
    print("=" * 75)
    
    if not Path(DB_PATH).exists():
        print(f"❌ FAIL: Database file not found at {DB_PATH}")
        sys.exit(1)
        
    con = duckdb.connect(DB_PATH, read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")
    
    failed_checks = 0

    # 1. STATION PLATFORMS & CORRIDOR ATTRIBUTION
    print("\n[1] Auditing Transit Corridors & Control Nodes...")
    corridor_dist = con.execute("""
        SELECT COALESCE(line_name, 'UNKNOWN') AS line_clean, COUNT(*) AS n, MIN(opening_year) AS open_yr
        FROM silver.stations_spatial
        GROUP BY line_clean
        ORDER BY n DESC;
    """).fetchall()
    
    lines_found = {row[0]: row[1] for row in corridor_dist}
    for name, count, yr in corridor_dist:
        safe_name = str(name)
        print(f"  • Corridor: {safe_name:<18} Platforms: {count:>3} | Timeline Baseline: {yr}")
        
    for required in ['Red Line', 'BL', 'BLE', 'Control_Matthews']:
        if required not in lines_found:
            print(f"  ❌ FAIL: Missing required corridor '{required}'.")
            failed_checks += 1
    if failed_checks == 0:
        print("  ✓ Pass: All primary treatment, proxy, and control platforms materialized.")

    # 2. MULTI-CORRIDOR PARCEL INTEGRITY
    print("\n[2] Auditing Multi-Corridor Parcel Fabric...")
    parcel_cnt = con.execute("SELECT COUNT(*) FROM silver.parcels_spatial;").fetchone()[0]
    print(f"  • Total Project Parcels: {parcel_cnt:,}")
    if parcel_cnt < 25000:
        print("  ❌ FAIL: Ingested parcel count is low for all study corridors.")
        failed_checks += 1
    else:
        print("  ✓ Pass: Parcel extraction covers full multi-corridor footprint.")

    tier_dist = con.execute("""
        SELECT transit_buffer_tier, COUNT(*) AS n, ROUND(AVG(dist_to_station_meters), 1) AS mean_m
        FROM silver.parcels_spatial
        GROUP BY transit_buffer_tier
        ORDER BY mean_m ASC;
    """).fetchall()
    print("  Catchment Tier Distribution:")
    for tier, n, mean_m in tier_dist:
        print(f"    - {tier:<20} {n:>7,} parcels (avg dist: {mean_m:>6.1f} m)")

    # 3. LONGITUDINAL SALES PANEL COVERAGE (2012–2026)
    print("\n[3] Auditing Sales Panel & Timeline Depth (2012–2026)...")
    sales_stats = con.execute("""
        SELECT 
            MIN(sale_year), 
            MAX(sale_year), 
            COUNT(DISTINCT sale_year), 
            COUNT(*) 
        FROM silver.sales_standardized;
    """).fetchone()
    min_yr, max_yr, distinct_yrs, total_sales = sales_stats
    print(f"  • Standardized Sales: {total_sales:,} across {distinct_yrs} years ({min_yr} to {max_yr})")
    
    if min_yr <= 2012 and max_yr == 2026 and distinct_yrs >= 14:
        print("  ✓ Pass: Longitudinal depth supports pre-construction, anticipation, and operational phases.")
    else:
        print(f"  ❌ FAIL: Sales timeline does not span 2012–2026 (Found: {min_yr}–{max_yr}, {distinct_yrs} years).")
        failed_checks += 1

    # 4. GOLD HEDONIC & DID SAMPLE VIEW
    print("\n[4] Auditing Gold Layer Econometric View (gold.hedonic_sample)...")
    gold_stats = con.execute("""
        SELECT 
            COUNT(*) AS total_obs,
            SUM(in_walkshed) AS treated_walkshed_obs,
            SUM(era_pre_construction) AS pre_const_obs,
            SUM(era_anticipation) AS anticipation_obs,
            SUM(era_revenue_service) AS revenue_obs
        FROM gold.hedonic_sample;
    """).fetchone()
    obs, walkshed_n, pre_n, ant_n, rev_n = gold_stats
    print(f"  • Total Modeling Observations: {obs:,}")
    print(f"  • Pre-Construction (2012–2013): {pre_n:,}")
    print(f"  • Anticipation Phase (2014–2017): {ant_n:,}")
    print(f"  • Revenue Service (2018–2026): {rev_n:,}")
    
    if obs > 0 and pre_n > 0 and ant_n > 0 and rev_n > 0:
        print("  ✓ Pass: Temporal partitions populated across all policy regimes.")
    else:
        print("  ❌ FAIL: Gold layer missing observation coverage in one or more policy eras.")
        failed_checks += 1

    # 5. GOLD MARKET LIQUIDITY & TRANSACTION VELOCITY VIEW
    print("\n[5] Auditing Market Liquidity & Turnover View (gold.transaction_velocity)...")
    
    # Calculate expected cell count dynamically (physical strata * 15 calendar years)
    expected_cells = con.execute("""
        WITH strata AS (
            SELECT DISTINCT 
                nearest_line, 
                CASE WHEN dist_to_station_ft <= 2640.0 THEN 1 ELSE 0 END AS in_walkshed, 
                zoning_category
            FROM silver.parcels_spatial
        )
        SELECT COUNT(*) * 15 FROM strata;
    """).fetchone()[0]

    velocity_stats = con.execute("""
        SELECT 
            COUNT(*) AS total_cells,
            SUM(tx_count) AS total_counted_tx,
            MIN(turnover_rate_pct),
            MAX(turnover_rate_pct),
            COUNT(CASE WHEN total_parcels <= 0 THEN 1 END) AS zero_denoms
        FROM gold.transaction_velocity;
    """).fetchone()
    v_cells, v_tx, min_t, max_t, zero_denoms = velocity_stats
    print(f"  • Aggregated Stratum Cells: {v_cells} (Expected: {expected_cells})")
    print(f"  • Total Transactions Accounted: {v_tx:,}")
    print(f"  • Turnover Rate Range: {min_t:.2f}% to {max_t:.2f}%")

    if v_cells == expected_cells and zero_denoms == 0 and v_tx > 0:
        print("  ✓ Pass: Balanced stratum-year panel verified with zero empty denominators.")
    else:
        print(f"  ❌ FAIL: Velocity panel audit failed (Found: {v_cells}, Expected: {expected_cells}, Zero-denoms: {zero_denoms}).")
        failed_checks += 1

    # 6. DOCUMENTATION & FIGURE ARTIFACTS
    print("\n[6] Auditing Compiled Documentation & Figure Artifacts...")
    expected_figures = [
        FIG_DIR / "did_parallel_trends.png",
        FIG_DIR / "gold_modeling_summary.png",
        FIG_DIR / "liquidity_velocity_shocks.png",
        FIG_DIR / "silver_feature_store_summary.png"
    ]
    for fig in expected_figures:
        if fig.exists() and fig.stat().st_size > 0:
            print(f"  • Figure: {fig.name:<32} ({fig.stat().st_size / 1024:.1f} KB) ✓")
        else:
            print(f"  ❌ FAIL: Missing or empty figure artifact: {fig}")
            failed_checks += 1

    expected_tables = [MD_LIQUIDITY, MD_REGRESSION]
    for tbl in expected_tables:
        if tbl.exists() and tbl.stat().st_size > 0:
            print(f"  • Table:  {tbl.name:<32} ✓")
        else:
            print(f"  ❌ FAIL: Missing summary table artifact: {tbl}")
            failed_checks += 1

    if HTML_PATH.exists():
        html_size = HTML_PATH.stat().st_size / 1024.0
        print(f"  • HTML:   {HTML_PATH.name:<32} ({html_size:.1f} KB) ✓")
    else:
        print("  ❌ FAIL: docs/index.html not found.")
        failed_checks += 1

    con.close()

    print("\n" + "=" * 75)
    if failed_checks == 0:
        print("🎯 ALL PIPELINE, MODELING & ARTIFACT CHECKS PASSED.")
        print("=" * 75)
        return 0
    else:
        print(f"⚠️  VALIDATION FAILED: {failed_checks} check(s) did not pass.")
        print("=" * 75)
        return 1

if __name__ == "__main__":
    sys.exit(run_checks())
