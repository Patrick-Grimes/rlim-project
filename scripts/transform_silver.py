import duckdb

DB_PATH = "data/transit_warehouse.duckdb"

def build_silver_layer():
    print("--- Materializing Silver Layer (Sprint 2 Feature Store) ---")
    con = duckdb.connect(DB_PATH)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver;")

    # 1. Inflation Index Table (BLS CPI-U Annual Averages normalized to 2026)
    print("  Creating silver.cpi_deflator (Base Year = 2026)...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.cpi_deflator AS
    SELECT * FROM (VALUES
        (2018, 251.107),
        (2019, 255.657),
        (2020, 258.811),
        (2021, 270.970),
        (2022, 292.655),
        (2023, 304.702),
        (2024, 314.175),
        (2025, 323.600),
        (2026, 333.300)
    ) AS t(sale_year, cpi_index);
    """)

    # 2. Transform Stations
    print("  Reprojecting stations to EPSG:2264 (using OGC:CRS84)...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.stations_spatial AS
    SELECT 
        COALESCE(line_name, 'BL') || '_' || stop_id AS station_uid,
        stop_id,
        stop_name,
        line_name,
        is_planned,
        ST_Transform(geom, 'OGC:CRS84', 'EPSG:2264') AS geom_2264
    FROM bronze.gtfs_stops;
    """)

    # 3. Transform Parcels with Distances, Zoning, and Walkshed Tiers
    print("  Reprojecting parcels and computing transit proximity...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.parcels_spatial AS
    WITH transformed_parcels AS (
        SELECT 
            parcel_id,
            nc_pin,
            shape_area_sqft,
            calc_acreage,
            raw_zoning,
            CASE 
                WHEN raw_zoning ILIKE '%TOD%' THEN 'TOD'
                WHEN raw_zoning ILIKE '%R-%' OR raw_zoning ILIKE '%SFR%' THEN 'SFR'
                WHEN raw_zoning ILIKE '%UR%' OR raw_zoning ILIKE '%MUDD%' THEN 'MFR'
                ELSE 'OTHER_COMMERCIAL'
            END AS zoning_category,
            ST_Transform(ST_Centroid(geom), 'OGC:CRS84', 'EPSG:2264') AS centroid_2264
        FROM bronze.parcels
    ),
    cbd_reference AS (
        SELECT ST_Transform(ST_Point(-80.8431, 35.2271), 'OGC:CRS84', 'EPSG:2264') AS cbd_geom_2264
    ),
    ranked_distances AS (
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
        FROM transformed_parcels p
        CROSS JOIN cbd_reference cbd
        CROSS JOIN silver.stations_spatial s
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
    FROM ranked_distances
    WHERE rn = 1;
    """)

    # 4. Standardize Sales to Real 2026 Dollars and Compute PPSF
    print("  Materializing silver.sales_standardized with CPI deflation...")
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
    WHERE s.sale_price >= 10000; -- Screen non-arm's-length nominal transfers
    """)

    # 5. Validation Summaries
    print("\n✓ Silver Spatial Buffer Summary:")
    print(con.execute("""
    SELECT 
        transit_buffer_tier,
        COUNT(*) AS parcel_count,
        ROUND(AVG(dist_to_station_meters), 1) AS avg_dist_m,
        ROUND(AVG(dist_to_cbd_ft / 5280.0), 2) AS avg_dist_cbd_mi
    FROM silver.parcels_spatial
    GROUP BY transit_buffer_tier
    ORDER BY parcel_count DESC;
    """).fetchdf())

    print("\n✓ Nearest Station Counts by Line:")
    print(con.execute("""
    SELECT nearest_line, nearest_station_name, COUNT(*) AS parcel_count
    FROM silver.parcels_spatial
    GROUP BY nearest_line, nearest_station_name
    ORDER BY parcel_count DESC
    LIMIT 5;
    """).fetchdf())

    con.close()

if __name__ == "__main__":
    build_silver_layer()
