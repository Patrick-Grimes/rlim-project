import subprocess
import duckdb

DB_PATH = "data/transit_warehouse.duckdb"

def build_silver_layer():
    print("--- Materializing Multi-Corridor Silver Feature Store ---")
    con = duckdb.connect(DB_PATH)
    con.install_extension("spatial")
    con.load_extension("spatial")
    con.execute("CREATE SCHEMA IF NOT EXISTS silver;")

    # 1. Official BLS CPI-U Index Series (2012–2026, Base Year = 2026 at 333.300)
    con.execute("""
    CREATE OR REPLACE TABLE silver.cpi_deflator (
        sale_year INTEGER PRIMARY KEY,
        cpi_index DOUBLE
    );
    DELETE FROM silver.cpi_deflator;
    INSERT INTO silver.cpi_deflator VALUES
        (2012, 229.594),
        (2013, 232.957),
        (2014, 236.736),
        (2015, 237.017),
        (2016, 240.007),
        (2017, 245.120),
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

    # 2. Stations Spatial
    con.execute("""
    CREATE OR REPLACE TABLE silver.stations_spatial AS
    SELECT 
        COALESCE(line_name, 'CATS') || '_' || stop_id AS station_uid,
        stop_id,
        stop_name,
        line_name,
        is_planned,
        opening_year,
        ST_Transform(geom, 'OGC:CRS84', 'EPSG:2264') AS geom_2264
    FROM bronze.gtfs_stops;
    """)

    # 3. Parcels Spatial Proximity (Red Line, Blue Line, & Matched Control)
    print("  Calculating Euclidean spatial metrics...")
    con.execute("""
    CREATE OR REPLACE TABLE silver.parcels_spatial AS
    WITH parcel_base AS (
        SELECT 
            parcel_id,
            nc_pin,
            shape_area_sqft,
            calc_acreage,
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
            s.station_uid AS nearest_station_id,
            s.stop_name AS nearest_station_name,
            s.line_name AS nearest_line,
            s.is_planned AS nearest_is_planned,
            s.opening_year,
            ROUND(ST_Distance(p.centroid_2264, s.geom_2264), 1) AS dist_to_station_ft,
            ROUND(ST_Distance(p.centroid_2264, s.geom_2264) * 0.3048, 1) AS dist_to_station_meters,
            ROW_NUMBER() OVER (
                PARTITION BY p.parcel_id 
                ORDER BY ST_Distance(p.centroid_2264, s.geom_2264) ASC
            ) AS rn
        FROM parcel_base p
        CROSS JOIN silver.stations_spatial s
        WHERE s.line_name IN ('Red Line', 'BL', 'BLE', 'Control_Matthews')
    )
    SELECT 
        p.parcel_id,
        p.nc_pin,
        p.shape_area_sqft,
        p.calc_acreage,
        p.zoning_category,
        ROUND(ST_Distance(p.centroid_2264, cbd.cbd_geom_2264), 1) AS dist_to_cbd_ft,
        rs.nearest_station_id,
        rs.nearest_station_name,
        rs.nearest_line,
        rs.nearest_is_planned,
        rs.opening_year,
        rs.dist_to_station_ft,
        rs.dist_to_station_meters,
        CASE 
            WHEN rs.dist_to_station_ft <= 1320 THEN 'QUARTER_MILE_WALK'
            WHEN rs.dist_to_station_ft <= 2640 THEN 'HALF_MILE_WALK'
            WHEN rs.dist_to_station_ft <= 5280 THEN 'ONE_MILE_ACCESS'
            ELSE 'OUTSIDE_WALKSHED'
        END AS transit_buffer_tier,
        CURRENT_TIMESTAMP AS processed_timestamp
    FROM parcel_base p
    CROSS JOIN cbd_reference cbd
    INNER JOIN ranked_stations rs ON p.parcel_id = rs.parcel_id AND rs.rn = 1;
    """)

    # 4. Standardized Sales (2012–2026 Deflated to 2026 USD)
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
    print(f"\n✓ Silver layer complete: {p_count:,} parcels | {s_count:,} deflated sales (2012–2026).")
    con.close()

    print("\nRebuilding docs/index.html...")
    subprocess.run(["python", "scripts/build_docs.py"], check=True)

if __name__ == "__main__":
    build_silver_layer()
