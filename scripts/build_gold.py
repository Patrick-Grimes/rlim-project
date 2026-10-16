import duckdb

DB_PATH = "data/transit_warehouse.duckdb"

def main():
    print("Building gold.hedonic_sample with anticipation and DiD event indicators...")
    con = duckdb.connect(DB_PATH)
    con.execute("CREATE SCHEMA IF NOT EXISTS gold;")

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
        -- Event Study Indicators
        CASE WHEN s.sale_year < 2014 THEN 1 ELSE 0 END AS era_pre_construction,
        CASE WHEN s.sale_year BETWEEN 2014 AND 2017 THEN 1 ELSE 0 END AS era_anticipation,
        CASE WHEN s.sale_year >= 2018 THEN 1 ELSE 0 END AS era_revenue_service
    FROM silver.sales_standardized s
    JOIN silver.parcels_spatial p ON s.parcel_id = p.parcel_id
    WHERE s.ppsf_real_2026 > 0;
    """)

    cnt = con.execute("SELECT count(*) FROM gold.hedonic_sample;").fetchone()[0]
    print(f"✓ gold.hedonic_sample ready with {cnt:,} multi-era observations.")
    con.close()

if __name__ == "__main__":
    main()
