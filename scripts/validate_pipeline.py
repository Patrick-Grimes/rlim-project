import sys
from pathlib import Path
import duckdb

DB_PATH = "data/transit_warehouse.duckdb"
HTML_PATH = Path("docs/index.html")

def run_checks():
    print("=" * 70)
    print("RLIM SPRINT 3: MULTI-CORRIDOR & DID PANEL VALIDATION AUDIT")
    print("=" * 70)
    
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
        print("  ❌ FAIL: Ingested parcel count is suspiciously low for all 4 corridors.")
        failed_checks += 1
    else:
        print("  ✓ Pass: Parcel extraction covers full multi-corridor footprint.")

    # Catchment balance
    tier_dist = con.execute("""
        SELECT transit_buffer_tier, COUNT(*) AS n, ROUND(AVG(dist_to_station_meters), 1) AS mean_m
        FROM silver.parcels_spatial
        GROUP BY transit_buffer_tier
        ORDER BY mean_m ASC;
    """).fetchall()
    print("\n  Catchment Tier Distribution:")
    for tier, n, mean_m in tier_dist:
        print(f"    - {tier:<20} {n:>7,} parcels (avg dist: {mean_m:>6.1f} m)")

    # 3. LONGITUDINAL SALES PANEL COVERAGE (2012–2026)
    print("\n[3] Auditing Sales Panel & Anticipation Windows (2012–2026)...")
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
        print("  ✓ Pass: Event study partitions populated across all temporal regimes.")
    else:
        print("  ❌ FAIL: Gold layer missing observation coverage in one or more event eras.")
        failed_checks += 1

    # 5. DOCUMENTATION ARTIFACTS
    print("\n[5] Auditing Compiled Documentation...")
    if HTML_PATH.exists():
        html_size = HTML_PATH.stat().st_size / 1024.0
        print(f"  ✓ Pass: docs/index.html verified ({html_size:.1f} KB).")
    else:
        print(f"  ❌ FAIL: docs/index.html not found.")
        failed_checks += 1

    con.close()

    print("\n" + "=" * 70)
    if failed_checks == 0:
        print("🎯 ALL MULTI-CORRIDOR & DID AUDIT CHECKS PASSED.")
        print("=" * 70)
        return 0
    else:
        print(f"⚠️  VALIDATION FAILED: {failed_checks} check(s) did not pass.")
        print("=" * 70)
        return 1

if __name__ == "__main__":
    sys.exit(run_checks())
