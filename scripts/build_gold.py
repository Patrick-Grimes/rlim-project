"""
scripts/build_gold.py
Materializes Gold analytical views in DuckDB:
  - gold.hedonic_sample: Arms-length transactions joined with spatial covariates
  - gold.transaction_velocity: Balanced stratum-year panel tracking market liquidity
"""

import duckdb

DB_PATH = "data/transit_warehouse.duckdb"

def main():
    print("=" * 75)
    print("RLIM: MATERIALIZING BALANCED GOLD LAYER")
    print("=" * 75)
    
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA IF NOT EXISTS gold;")

    # 1. Price-Level Hedonic & DiD Modeling Sample
    print("\n--- 1. Materializing gold.hedonic_sample ---")
    con.execute("""
    CREATE OR REPLACE VIEW gold.hedonic_sample AS
    SELECT 
        s.sale_id,
        s.parcel_id,
        s.sale_year,
        s.nominal_price,
        s.real_price_2026,
        s.ppsf_real_2026,
        LN(s.real_price_2026) AS log_real_price,
        LN(s.ppsf_real_2026) AS log_real_ppsf,
        p.shape_area_sqft,
        p.calc_acreage,
        p.zoning_category,
        p.dist_to_cbd_ft / 5280.0 AS dist_to_cbd_miles,
        p.dist_to_station_meters / 1000.0 AS dist_station_km,
        p.nearest_line,
        p.nearest_station_name,
        p.opening_year,
        p.dist_to_station_meters,
        p.dist_to_station_ft,
        p.transit_buffer_tier,
        CASE WHEN p.dist_to_station_ft <= 2640.0 THEN 1 ELSE 0 END AS in_walkshed,
        CASE WHEN p.nearest_line IN ('BL', 'BLE') THEN 1 ELSE 0 END AS is_blue_line,
        CASE WHEN p.nearest_line = 'Control_Matthews' THEN 1 ELSE 0 END AS is_control_corridor,
        CASE WHEN p.nearest_line = 'Red Line' THEN 1 ELSE 0 END AS is_red_line,
        CASE WHEN s.sale_year < 2014 THEN 1 ELSE 0 END AS era_pre_construction,
        CASE WHEN s.sale_year BETWEEN 2014 AND 2017 THEN 1 ELSE 0 END AS era_anticipation,
        CASE WHEN s.sale_year >= 2018 THEN 1 ELSE 0 END AS era_revenue_service
    FROM silver.sales_standardized s
    JOIN silver.parcels_spatial p ON s.parcel_id = p.parcel_id
    WHERE s.ppsf_real_2026 > 0;
    """)
    cnt_price = con.execute("SELECT count(*) FROM gold.hedonic_sample;").fetchone()[0]
    print(f"  ✓ gold.hedonic_sample ready with {cnt_price:,} observations.")

    # 2. Transaction Velocity Balanced Panel (Preserves Zero-Count Cells)
    print("\n--- 2. Materializing gold.transaction_velocity ---")
    con.execute("""
    CREATE OR REPLACE VIEW gold.transaction_velocity AS
    WITH parcel_denominators AS (
        SELECT 
            nearest_line,
            CASE WHEN dist_to_station_ft <= 2640.0 THEN 1 ELSE 0 END AS in_walkshed,
            zoning_category,
            COUNT(*) AS total_parcels
        FROM silver.parcels_spatial
        GROUP BY 
            nearest_line, 
            CASE WHEN dist_to_station_ft <= 2640.0 THEN 1 ELSE 0 END, 
            zoning_category
    ),
    calendar_years AS (
        SELECT DISTINCT sale_year 
        FROM silver.sales_standardized 
        WHERE sale_year BETWEEN 2012 AND 2026
    ),
    stratum_grid AS (
        SELECT 
            pd.nearest_line,
            pd.in_walkshed,
            pd.zoning_category,
            pd.total_parcels,
            cy.sale_year
        FROM parcel_denominators pd
        CROSS JOIN calendar_years cy
    ),
    sales_counts AS (
        SELECT 
            p.nearest_line,
            CASE WHEN p.dist_to_station_ft <= 2640.0 THEN 1 ELSE 0 END AS in_walkshed,
            p.zoning_category,
            s.sale_year,
            COUNT(*) AS tx_count,
            ROUND(MEDIAN(s.ppsf_real_2026), 2) AS median_real_ppsf,
            ROUND(AVG(s.ppsf_real_2026), 2) AS mean_real_ppsf
        FROM silver.sales_standardized s
        JOIN silver.parcels_spatial p ON s.parcel_id = p.parcel_id
        GROUP BY 
            p.nearest_line, 
            CASE WHEN p.dist_to_station_ft <= 2640.0 THEN 1 ELSE 0 END, 
            p.zoning_category, 
            s.sale_year
    )
    SELECT 
        sg.nearest_line,
        sg.in_walkshed,
        sg.zoning_category,
        sg.sale_year,
        COALESCE(sc.tx_count, 0) AS tx_count,
        sg.total_parcels,
        ROUND((COALESCE(sc.tx_count, 0)::DOUBLE / NULLIF(sg.total_parcels, 0)) * 100.0, 2) AS turnover_rate_pct,
        sc.median_real_ppsf,
        sc.mean_real_ppsf,
        CASE 
            WHEN sg.sale_year <= 2013 THEN 'Pre-Grant Baseline'
            WHEN sg.sale_year BETWEEN 2014 AND 2017 THEN 'Anticipation / Construction'
            ELSE 'Revenue Service'
        END AS policy_era
    FROM stratum_grid sg
    LEFT JOIN sales_counts sc 
        ON sg.nearest_line = sc.nearest_line 
       AND sg.in_walkshed = sc.in_walkshed 
       AND sg.zoning_category = sc.zoning_category
       AND sg.sale_year = sc.sale_year;
    """)
    cnt_velocity = con.execute("SELECT count(*) FROM gold.transaction_velocity;").fetchone()[0]
    print(f"  ✓ gold.transaction_velocity ready with {cnt_velocity:,} stratum-year observations.")

    con.close()
    print("\n✓ Gold layer rebuild complete.")

if __name__ == "__main__":
    main()
