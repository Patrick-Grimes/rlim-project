import json
from pathlib import Path
import urllib.parse
import urllib.request
import duckdb

DB_PATH = "data/transit_warehouse.duckdb"
BRONZE_DIR = Path("data/bronze")
PARCELS_DIR = BRONZE_DIR / "parcels"
GTFS_DIR = BRONZE_DIR / "gtfs"

for d in [PARCELS_DIR, GTFS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

def download_geojson(base_url: str, params: dict, output_file: Path) -> Path:
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    print(f"Fetching: {output_file.name}...")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) CapstoneTransitETL/1.0"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        content = resp.read()
        with open(output_file, "wb") as f:
            f.write(content)
    print(f"  ✓ Saved {len(content) / 1024:.1f} KB to {output_file.name}")
    return output_file

def fetch_and_load_stations(con):
    print("\n--- 1. Fetching Live CATS Station Geometries & Adding Red Line ---")
    base_url = "https://gis.charlottenc.gov/arcgis/rest/services/CATS/TransitStationDevelopmentPublic/MapServer/0/query"
    params = {
        "where": "Station IS NOT NULL",
        "outFields": "Corridor,Station,ProjectName,WalkDistance",
        "outSR": "4326",
        "resultRecordCount": "2000",
        "f": "geojson",
    }
    raw_file = GTFS_DIR / "cats_stations_live.geojson"
    download_geojson(base_url, params, raw_file)

    con.execute(f"""
    CREATE OR REPLACE TABLE bronze.gtfs_stops AS
    WITH raw_features AS (
        SELECT 
            "Station"::VARCHAR AS station_code,
            "Corridor"::VARCHAR AS line_name,
            geom
        FROM ST_Read('{raw_file.as_posix()}')
        WHERE geom IS NOT NULL AND "Station" IS NOT NULL
    ),
    station_clusters AS (
        SELECT 
            station_code,
            line_name,
            ST_Centroid(ST_Union_Agg(geom)) AS station_geom
        FROM raw_features
        GROUP BY station_code, line_name
    )
    SELECT 
        station_code AS stop_id,
        station_code || ' Station' AS stop_name,
        ST_Y(station_geom) AS stop_lat,
        ST_X(station_geom) AS stop_lon,
        line_name,
        station_geom AS geom,
        FALSE AS is_planned,
        CURRENT_TIMESTAMP AS ingest_timestamp
    FROM station_clusters;
    """)

    # Seed official planned Red Line stops (North Corridor Commuter Rail)
    con.execute("""
    INSERT INTO bronze.gtfs_stops (stop_id, stop_name, stop_lat, stop_lon, line_name, geom, is_planned)
    VALUES 
        ('RL_MT_MOURNE', 'Mt. Mourne Station', 35.5312, -80.8268, 'Red Line', ST_Point(-80.8268, 35.5312), TRUE),
        ('RL_DAVIDSON', 'Davidson Main & Griffith Station', 35.4993, -80.8431, 'Red Line', ST_Point(-80.8431, 35.4993), TRUE),
        ('RL_CORNELIUS', 'Cornelius Town Center Station', 35.4805, -80.8602, 'Red Line', ST_Point(-80.8602, 35.4805), TRUE),
        ('RL_NORTHCROSS', 'Northcross Station', 35.4497, -80.8683, 'Red Line', ST_Point(-80.8683, 35.4497), TRUE),
        ('RL_HUNTERSVILLE', 'Huntersville Downtown Station', 35.4102, -80.8435, 'Red Line', ST_Point(-80.8435, 35.4102), TRUE);
    """)

    count = con.execute("SELECT count(*) FROM bronze.gtfs_stops;").fetchone()[0]
    print(f"  ✓ Materialized {count} total stations into bronze.gtfs_stops (including Red Line).")

def fetch_and_load_parcels(con, batch_size=2000):
    print(f"\n--- 2. Fetching Live Mecklenburg Corridor Parcels (Batch: {batch_size}) ---")
    base_url = "https://gis.charlottenc.gov/arcgis/rest/services/CountyData/Parcels/MapServer/0/query"
    
    # Bounding envelope centered on the Huntersville-Cornelius-Davidson transit spine
    params = {
        "where": "Shape.STArea() > 500",
        "geometry": "-80.875,35.430,-80.825,35.520",
        "geometryType": "esriGeometryEnvelope",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outFields": "PID,NC_PIN,Shape.STArea()",
        "outSR": "4326",
        "resultRecordCount": str(batch_size),
        "f": "geojson",
    }
    raw_file = PARCELS_DIR / f"parcels_corridor_{batch_size}.geojson"
    download_geojson(base_url, params, raw_file)

    con.execute(f"""
    CREATE OR REPLACE TABLE bronze.parcels AS
    SELECT 
        "PID"::VARCHAR AS parcel_id,
        "NC_PIN"::VARCHAR AS nc_pin,
        CASE 
            WHEN (ROW_NUMBER() OVER ()) % 10 = 0 THEN 'TOD-M'
            WHEN (ROW_NUMBER() OVER ()) % 4 = 0 THEN 'MFR'
            WHEN (ROW_NUMBER() OVER ()) % 7 = 0 THEN 'COMM'
            ELSE 'SFR'
        END AS raw_zoning,
        "Shape.STArea()"::DOUBLE AS shape_area_sqft,
        ROUND("Shape.STArea()"::DOUBLE / 43560.0, 4) AS calc_acreage,
        geom,
        CURRENT_TIMESTAMP AS ingest_timestamp
    FROM ST_Read('{raw_file.as_posix()}')
    WHERE geom IS NOT NULL;
    """)
    count = con.execute("SELECT count(*) FROM bronze.parcels;").fetchone()[0]
    print(f"  ✓ Materialized {count} corridor parcels into bronze.parcels")

def seed_bronze_sales(con):
    print("\n--- 3. Seeding Longitudinal Historical Sales (2018-2026) ---")
    con.execute("""
    CREATE TABLE IF NOT EXISTS bronze.property_sales (
        sale_id VARCHAR PRIMARY KEY,
        parcel_id VARCHAR,
        sale_date DATE,
        sale_price DOUBLE,
        qualified_sale_flag VARCHAR,
        deed_book VARCHAR,
        deed_page VARCHAR,
        ingest_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # Generate multi-year repeat transactions spanning 2018 to 2026 across corridor parcels
    con.execute("""
    DELETE FROM bronze.property_sales;
    
    INSERT INTO bronze.property_sales (sale_id, parcel_id, sale_date, sale_price, qualified_sale_flag, deed_book, deed_page)
    WITH sample_parcels AS (
        SELECT parcel_id, shape_area_sqft, ROW_NUMBER() OVER () AS rnum
        FROM bronze.parcels
        LIMIT 600
    ),
    years AS (
        SELECT unnest([2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]) AS yr
    )
    SELECT 
        'SALE_' || p.parcel_id || '_' || y.yr AS sale_id,
        p.parcel_id,
        MAKE_DATE(y.yr, 1 + ((p.rnum * 7) % 11)::INT, 1 + ((p.rnum * 13) % 27)::INT) AS sale_date,
        -- Realistic appreciation gradient: nominal growth + area scaling + variation
        ROUND(
            (280000.0 * POWER(1.045, y.yr - 2018)) 
            + (p.shape_area_sqft * 4.5) 
            + (((p.rnum * 104729) % 50000) - 25000), 
            2
        ) AS sale_price,
        'Y' AS qualified_sale_flag,
        'BK_' || y.yr AS deed_book,
        'PG_' || p.rnum AS deed_page
    FROM sample_parcels p
    CROSS JOIN years y
    WHERE (p.rnum + y.yr) % 2 = 0; -- Produces varied transaction frequencies
    """)
    count = con.execute("SELECT count(*) FROM bronze.property_sales;").fetchone()[0]
    print(f"  ✓ Seeded {count} longitudinal sales transactions across 2018-2026.")

def main():
    con = duckdb.connect(DB_PATH)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze;")

    fetch_and_load_stations(con)
    fetch_and_load_parcels(con, batch_size=2000)
    seed_bronze_sales(con)

    con.close()
    print("\n✓ Live Bronze ingestion finished.")

if __name__ == "__main__":
    main()
