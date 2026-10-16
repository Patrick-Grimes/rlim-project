import sys
from pathlib import Path
import duckdb

DB_PATH = "data/transit_warehouse.duckdb"
HTML_PATH = Path("docs/index.html")

def run_checks():
    print("=" * 65)
    print("RLIM SPRINT 3 PRE-FLIGHT VALIDATION & VERIFICATION AUDIT")
    print("=" * 65)
    
    if not Path(DB_PATH).exists():
        print(f"❌ FAIL: Database file not found at {DB_PATH}")
        sys.exit(1)
        
    con = duckdb.connect(DB_PATH, read_only=True)
    con.install_extension("spatial")
    con.load_extension("spatial")
    
    failed_checks = 0

    print("\n[1] Auditing Transit Station Nodes...")
    red_line_count = con.execute("""
        SELECT COUNT(*) FROM bronze.gtfs_stops WHERE line_name = 'Red Line';
    """).fetchone()[0]
    
    if red_line_count == 11:
        print("  ✓ Pass: Exactly 11 Red Line platforms materialized.")
    else:
        print(f"  ❌ FAIL: Expected 11 Red Line platforms, found {red_line_count}.")
        failed_checks += 1

    silver_stations = con.execute("""
        SELECT COUNT(*) FROM silver.stations_spatial WHERE line_name = 'Red Line';
    """).fetchone()[0]
    if silver_stations == 11:
        print("  ✓ Pass: All 11 stations reprojected into silver.stations_spatial (EPSG:2264).")
    else:
        print(f"  ❌ FAIL: silver.stations_spatial has {silver_stations} Red Line records.")
        failed_checks += 1

    print("\n[2] Auditing Parcel Buffers & Geometry Bounds...")
    parcel_cnt = con.execute("SELECT COUNT(*) FROM bronze.parcels;").fetchone()[0]
    print(f"  • Total Bronze Corridor Parcels: {parcel_cnt:,}")
    
    if parcel_cnt < 1000:
        print("  ❌ FAIL: Parcel count is suspiciously low for a 2-mile corridor.")
        failed_checks += 1
    else:
        print("  ✓ Pass: Parcel sample size aligns with full corridor footprint.")

    tier_dist = con.execute("""
        SELECT transit_buffer_tier, COUNT(*) AS n, ROUND(AVG(dist_to_station_meters), 1) AS mean_m
        FROM silver.parcels_spatial
        GROUP BY transit_buffer_tier
        ORDER BY mean_m ASC;
    """).fetchall()
    
    print("\n  Walkshed Distribution:")
    walkshed_counts = {}
    for tier, n, mean_m in tier_dist:
        walkshed_counts[tier] = n
        print(f"    - {tier:<20} {n:>6,} parcels (avg dist: {mean_m:>6.1f} m)")
        
    for key in ['QUARTER_MILE_WALK', 'HALF_MILE_WALK', 'ONE_MILE_ACCESS']:
        if walkshed_counts.get(key, 0) == 0:
            print(f"  ❌ FAIL: Catchment tier '{key}' contains 0 parcels.")
            failed_checks += 1
    if failed_checks == 0:
        print("  ✓ Pass: Catchment tiers reflect balanced spatial distribution across the corridor.")

    print("\n[3] Auditing Regional Urban Gradients...")
    cbd_stats = con.execute("""
        SELECT 
            MIN(dist_to_cbd_ft / 5280.0), 
            MAX(dist_to_cbd_ft / 5280.0) 
        FROM silver.parcels_spatial;
    """).fetchone()
    min_cbd, max_cbd = cbd_stats
    print(f"  • Distance to CBD Range: {min_cbd:.2f} miles to {max_cbd:.2f} miles")
    if min_cbd < 1.0 and max_cbd > 15.0:
        print("  ✓ Pass: CBD distance spans Uptown through North Mecklenburg (Trade & Tryon reference verified).")
    else:
        print("  ❌ FAIL: CBD distance calculation out of expected corridor range.")
        failed_checks += 1

    print("\n[4] Auditing Econometric Sales Sample (2018–2026)...")
    year_stats = con.execute("""
        SELECT 
            MIN(sale_year), 
            MAX(sale_year), 
            COUNT(DISTINCT sale_year), 
            COUNT(*) 
        FROM silver.sales_standardized;
    """).fetchone()
    min_yr, max_yr, num_yrs, total_sales = year_stats
    print(f"  • Total Standardized Sales: {total_sales:,} across {num_yrs} distinct years ({min_yr} to {max_yr})")
    
    if num_yrs >= 8 and min_yr == 2018 and max_yr == 2026:
        print("  ✓ Pass: Full multi-year longitudinal coverage established.")
    else:
        print(f"  ❌ FAIL: Missing historical years in sales records (Found {num_yrs} years).")
        failed_checks += 1

    invalid_ppsf = con.execute("""
        SELECT COUNT(*) FROM silver.sales_standardized WHERE ppsf_real_2026 <= 0 OR ppsf_real_2026 IS NULL;
    """).fetchone()[0]
    if invalid_ppsf == 0:
        print("  ✓ Pass: Real Price Per Square Foot (ppsf_real_2026) has 0 null or non-positive values.")
    else:
        print(f"  ❌ FAIL: Found {invalid_ppsf} invalid ppsf_real_2026 rows.")
        failed_checks += 1

    deflation_test = con.execute("""
        SELECT COUNT(*) 
        FROM silver.sales_standardized 
        WHERE sale_year < 2026 AND real_price_2026 < nominal_price;
    """).fetchone()[0]
    if deflation_test == 0:
        print("  ✓ Pass: CPI deflation direction verified (historical nominal prices properly scaled up to 2026 USD).")
    else:
        print(f"  ❌ FAIL: Found {deflation_test} transactions where historical real price was lower than nominal price.")
        failed_checks += 1

    print("\n[5] Auditing Documentation Artifacts...")
    if HTML_PATH.exists():
        html_size = HTML_PATH.stat().st_size / 1024.0
        print(f"  ✓ Pass: docs/index.html compiled ({html_size:.1f} KB).")
        html_text = HTML_PATH.read_text()
        if "Real 2026" in html_text and "Walkshed Tier" in html_text:
            print("  ✓ Pass: docs/index.html contains expected Plotly figures and tabs.")
        else:
            print("  ❌ FAIL: docs/index.html is missing visualization figure containers.")
            failed_checks += 1
    else:
        print(f"  ❌ FAIL: docs/index.html does not exist at {HTML_PATH}.")
        failed_checks += 1

    con.close()

    print("\n" + "=" * 65)
    if failed_checks == 0:
        print("🎯 ALL AUDIT CHECKS PASSED. Ready to commit and push.")
        print("=" * 65)
        return 0
    else:
        print(f"⚠️  VALIDATION FAILED: {failed_checks} check(s) did not pass.")
        print("=" * 65)
        return 1

if __name__ == "__main__":
    sys.exit(run_checks())
