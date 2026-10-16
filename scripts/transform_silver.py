import subprocess
import duckdb

DB_PATH = "data/transit_warehouse.duckdb"

def build_silver_layer():
    print("--- Materializing Silver Layer (Feature Store) ---")
    con = duckdb.connect(DB_PATH)
    con.install_extension("spatial")
    con.load_extension("spatial")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver;")

    # 1. BLS CPI Deflator (Base Year = 2026)
    print("  Creating silver.cpi_deflator...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.cpi_deflator (
        sale_year INTEGER PRIMARY KEY,
        cpi_index DOUBLE
    );
    DELETE FROM silver.cpi_deflator;
    INSERT INTO silver.cpi_deflator VALUES
        (2018, 251.107),
        (2019, 255.657),
        (2020, 258.811),
        (2021, 270.970),
        (2022, 292.655),
        (2023, 304.702),
        (2024, 314.175),
        (2025, 323.600),
        (2026, 333.300);
    """)

    # 2. Transform Stations to EPSG:2264
    print("  Reprojecting stations to EPSG:2264...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.stations_spatial AS
    SELECT 
        COALESCE(line_name, 'CATS') || '_' || stop_id AS station_uid,
        stop_id,
        stop_name,
        line_name,
        is_planned,
        ST_Transform(geom, 'OGC:CRS84', 'EPSG:2264') AS geom_2264
    FROM bronze.gtfs_stops;
    """)

    # 3. Transform Parcels with Distances, Zoning, and Walkshed Tiers
    # Using native EPSG:2264 centroid from bronze.parcels
    print("  Computing Euclidean distances to platform, CBD, and walkshed bands...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.parcels_spatial AS
    WITH parcel_base AS (
        SELECT 
            parcel_id,
            nc_pin,
            shape_area_sqft,
            calc_acreage,
            raw_zoning,
            CASE 
                WHEN UPPER(raw_zoning) LIKE '%TOD%' OR UPPER(raw_zoning) LIKE '%MIXED%' THEN 'TOD'
                WHEN UPPER(raw_zoning) LIKE '%COMM%' OR UPPER(raw_zoning) LIKE '%BUS%' OR UPPER(raw_zoning) LIKE '%IND%' THEN 'OTHER_COMMERCIAL'
                WHEN UPPER(raw_zoning) LIKE '%MULTI%' OR UPPER(raw_zoning) LIKE '%CONDO%' OR UPPER(raw_zoning) LIKE '%APART%' THEN 'MFR'
                ELSE 'SFR'
            END AS zoning_category,
            ST_Centroid(geom_2264) AS centroid_2264
        FROM bronze.parcels
    ),
    cbd_reference AS (
        SELECT ST_Transform(ST_Point(-80.8431, 35.2271), 'OGC:CRS84', 'EPSG:2264') AS cbd_geom_2264
    ),
    ranked_stations AS (
        SELECT 
            p.parcel_id,
            p.nc_pin,
            p.shape_area_sqft,
            p.calc_acreage,
            p.zoning_category,
            ROUND(ST_Distance(p.centroid_2264, cbd.cbd_geom_2264), 1) AS dist_to_cbd_ft,
            s.station_uid AS nearest_station_id,
            s.stop_name AS nearest_station_name,
            s.line_name AS nearest_line,
            s.is_planned AS nearest_is_planned,
            ROUND(ST_Distance(p.centroid_2264, s.geom_2264), 1) AS dist_to_station_ft,
            ROUND(ST_Distance(p.centroid_2264, s.geom_2264) * 0.3048, 1) AS dist_to_station_meters,
            ROW_NUMBER() OVER (
                PARTITION BY p.parcel_id 
                ORDER BY ST_Distance(p.centroid_2264, s.geom_2264) ASC
            ) AS rn
        FROM parcel_base p
        CROSS JOIN cbd_reference cbd
        CROSS JOIN silver.stations_spatial s
        WHERE s.line_name = 'Red Line'
    )
    SELECT 
        parcel_id,
        nc_pin,
        shape_area_sqft,
        calc_acreage,
        zoning_category,
        dist_to_cbd_ft,
        nearest_station_id,
        nearest_station_name,
        nearest_line,
        nearest_is_planned,
        dist_to_station_ft,
        dist_to_station_meters,
        CASE 
            WHEN dist_to_station_ft <= 1320 THEN 'QUARTER_MILE_WALK'
            WHEN dist_to_station_ft <= 2640 THEN 'HALF_MILE_WALK'
            WHEN dist_to_station_ft <= 5280 THEN 'ONE_MILE_ACCESS'
            ELSE 'OUTSIDE_WALKSHED'
        END AS transit_buffer_tier,
        CURRENT_TIMESTAMP AS processed_timestamp
    FROM ranked_stations
    WHERE rn = 1;
    """)

    # 4. Standardize Sales to Real 2026 Dollars and Compute PPSF
    print("  Materializing silver.sales_standardized with CPI adjustments...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.sales_standardized AS
    WITH cpi_2026 AS (
        SELECT cpi_index AS base_cpi FROM silver.cpi_deflator WHERE sale_year = 2026
    )
    SELECT 
        s.sale_id,
        s.parcel_id,
        s.sale_date,
        EXTRACT(YEAR FROM s.sale_date)::INTEGER AS sale_year,
        s.sale_price AS nominal_price,
        ROUND(s.sale_price * (base.base_cpi / d.cpi_index), 2) AS real_price_2026,
        p.shape_area_sqft,
        ROUND((s.sale_price * (base.base_cpi / d.cpi_index)) / NULLIF(p.shape_area_sqft, 0), 2) AS ppsf_real_2026,
        s.qualified_sale_flag,
        CURRENT_TIMESTAMP AS processed_timestamp
    FROM bronze.property_sales s
    JOIN silver.parcels_spatial p ON s.parcel_id = p.parcel_id
    JOIN silver.cpi_deflator d ON EXTRACT(YEAR FROM s.sale_date) = d.sale_year
    CROSS JOIN cpi_2026 base
    WHERE s.sale_price >= 10000;
    """)

    p_count = con.execute("SELECT count(*) FROM silver.parcels_spatial;").fetchone()[0]
    s_count = con.execute("SELECT count(*) FROM silver.sales_standardized;").fetchone()[0]
    print(f"\n✓ Silver layer complete: {p_count:,} parcels | {s_count:,} standardized sales.")
    con.close()

    print("\nRebuilding docs/index.html...")
    subprocess.run(["python", "scripts/build_docs.py"], check=True)

if __name__ == "__main__":
    build_silver_layer()
