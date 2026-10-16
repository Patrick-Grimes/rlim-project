import urllib.parse
import urllib.request
from pathlib import Path
import duckdb

DB_PATH = "data/transit_warehouse.duckdb"
BRONZE_DIR = Path("data/bronze")
PARCELS_DIR = BRONZE_DIR / "parcels"
GTFS_DIR = BRONZE_DIR / "gtfs"

for d in [PARCELS_DIR, GTFS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

def download_file(url: str, output_path: Path):
    print(f"Fetching: {output_path.name}...")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) RLIMCapStone/1.0"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        content = resp.read()
        with open(output_path, "wb") as f:
            f.write(content)
    print(f"  ✓ Saved {len(content) / 1024:.1f} KB.")

def ingest_stations(con):
    print("\n--- 1. Ingesting CATS Active Rail Platforms & 11 Red Line Stations ---")
    base_url = "https://gis.charlottenc.gov/arcgis/rest/services/CATS/TransitStationDevelopmentPublic/MapServer/0/query"
    params = {
        "where": "Station IS NOT NULL",
        "outFields": "Corridor,Station,ProjectName,WalkDistance",
        "outSR": "4326",
        "resultRecordCount": "2000",
        "f": "geojson",
    }
    geojson_path = GTFS_DIR / "cats_stations_live.geojson"
    download_file(f"{base_url}?{urllib.parse.urlencode(params)}", geojson_path)

    con.execute(f"""
    CREATE OR REPLACE TABLE bronze.gtfs_stops AS
    WITH raw_features AS (
        SELECT 
            "Station"::VARCHAR AS station_code,
            "Corridor"::VARCHAR AS line_name,
            geom
        FROM ST_Read('{geojson_path.as_posix()}')
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

    con.execute("""
    INSERT INTO bronze.gtfs_stops (stop_id, stop_name, stop_lat, stop_lon, line_name, geom, is_planned)
    VALUES 
        ('RL_MT_MOURNE', 'Mount Mourne', 35.5312, -80.8268, 'Red Line', ST_Point(-80.8268, 35.5312), TRUE),
        ('RL_DAVIDSON', 'Davidson', 35.4993, -80.8431, 'Red Line', ST_Point(-80.8431, 35.4993), TRUE),
        ('RL_CORNELIUS', 'Cornelius', 35.4805, -80.8602, 'Red Line', ST_Point(-80.8602, 35.4805), TRUE),
        ('RL_SAM_FURR', 'Sam Furr', 35.4495, -80.8680, 'Red Line', ST_Point(-80.8680, 35.4495), TRUE),
        ('RL_HUNTERSVILLE', 'Huntersville', 35.4102, -80.8435, 'Red Line', ST_Point(-80.8435, 35.4102), TRUE),
        ('RL_HAMBRIGHT', 'Hambright', 35.3780, -80.8450, 'Red Line', ST_Point(-80.8450, 35.3780), TRUE),
        ('RL_EASTFIELD', 'Eastfield', 35.3520, -80.8350, 'Red Line', ST_Point(-80.8350, 35.3520), TRUE),
        ('RL_HARRIS', 'Harris', 35.3180, -80.8380, 'Red Line', ST_Point(-80.8380, 35.3180), TRUE),
        ('RL_DERITA', 'Derita', 35.2840, -80.8050, 'Red Line', ST_Point(-80.8050, 35.2840), TRUE),
        ('RL_CAMP_NORTH_END', 'Camp North End', 35.2480, -80.8230, 'Red Line', ST_Point(-80.8230, 35.2480), TRUE),
        ('RL_GATEWAY', 'Charlotte Gateway Station', 35.2300, -80.8490, 'Red Line', ST_Point(-80.8490, 35.2300), TRUE);
    """)

    cnt = con.execute("SELECT count(*) FROM bronze.gtfs_stops;").fetchone()[0]
    print(f"  ✓ Materialized {cnt} transit platforms (including 11 Red Line stations).")

def ingest_parcels_from_shapefile(con):
    print("\n--- 2. Ingesting NC OneMap Mecklenburg Shapefile (2-Mile Corridor Filter) ---")
    shp_path = PARCELS_DIR / "nc_mecklenburg_parcels_poly.shp"
    if not shp_path.exists():
        raise FileNotFoundError(f"Missing {shp_path}.")

    con.execute("""
    CREATE OR REPLACE TEMP TABLE temp_corridor_union AS
    SELECT ST_Union_Agg(ST_Transform(geom, 'OGC:CRS84', 'EPSG:2264')) AS corridor_pts
    FROM bronze.gtfs_stops
    WHERE line_name = 'Red Line';
    """)

    print("  Executing spatial filter across 2-mile buffer (10,560 US Survey Feet)...")
    con.execute(f"""
    CREATE OR REPLACE TABLE bronze.parcels AS
    WITH raw_parcels AS (
        SELECT 
            PARNO::VARCHAR AS parcel_id,
            COALESCE(NPARNO::VARCHAR, PARNO::VARCHAR) AS nc_pin,
            COALESCE(PARUSEDESC::VARCHAR, 'UNCLASSIFIED') AS raw_zoning,
            COALESCE(GISACRES::DOUBLE, 0.25) AS calc_acreage,
            COALESCE(GISACRES::DOUBLE, 0.25) * 43560.0 AS shape_area_sqft,
            geom AS geom_2264,
            ST_Transform(geom, 'EPSG:2264', 'OGC:CRS84') AS geom,
            SALEDATE AS last_saledate,
            PARVAL::DOUBLE AS assessed_val,
            CURRENT_TIMESTAMP AS ingest_timestamp,
            ROW_NUMBER() OVER (PARTITION BY PARNO ORDER BY GISACRES DESC) AS rn
        FROM ST_Read('{shp_path.as_posix()}')
        WHERE geom IS NOT NULL AND PARNO IS NOT NULL AND GISACRES > 0.01
    )
    SELECT p.parcel_id, p.nc_pin, p.raw_zoning, p.calc_acreage, p.shape_area_sqft, p.geom_2264, p.geom, p.last_saledate, p.assessed_val, p.ingest_timestamp
    FROM raw_parcels p, temp_corridor_union u
    WHERE p.rn = 1 AND ST_DWithin(p.geom_2264, u.corridor_pts, 10560.0);
    """)

    cnt = con.execute("SELECT count(*) FROM bronze.parcels;").fetchone()[0]
    print(f"  ✓ Filtered and materialized {cnt:,} unique corridor parcels.")

def seed_sales(con):
    print("\n--- 3. Seeding Longitudinal Repeat Sales Sample (2018-2026) ---")
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

    DELETE FROM bronze.property_sales;

    INSERT INTO bronze.property_sales (sale_id, parcel_id, sale_date, sale_price, qualified_sale_flag, deed_book, deed_page)
    WITH sample_parcels AS (
        SELECT 
            parcel_id, 
            shape_area_sqft, 
            COALESCE(assessed_val, 320000.0) AS base_val,
            ROW_NUMBER() OVER (ORDER BY parcel_id) AS rnum
        FROM bronze.parcels
        WHERE calc_acreage BETWEEN 0.05 AND 10.0
    ),
    years AS (
        SELECT unnest([2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]) AS yr
    )
    SELECT 
        'SALE_' || p.parcel_id || '_' || y.yr || '_' || p.rnum AS sale_id,
        p.parcel_id,
        MAKE_DATE(y.yr, 1 + ((p.rnum * 7) % 11)::INT, 1 + ((p.rnum * 13) % 27)::INT) AS sale_date,
        ROUND(
            (p.base_val * POWER(1.048, y.yr - 2018)) 
            + (((p.rnum * 104729) % 40000) - 20000), 
            2
        ) AS sale_price,
        'Y' AS qualified_sale_flag,
        'BK_' || y.yr AS deed_book,
        'PG_' || p.rnum AS deed_page
    FROM sample_parcels p
    CROSS JOIN years y
    WHERE (p.rnum + y.yr) % 3 = 0;
    """)

    cnt = con.execute("SELECT count(*) FROM bronze.property_sales;").fetchone()[0]
    print(f"  ✓ Materialized {cnt:,} multi-year sales transactions.")

def main():
    con = duckdb.connect(DB_PATH)
    con.install_extension("spatial")
    con.load_extension("spatial")
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze;")

    ingest_stations(con)
    ingest_parcels_from_shapefile(con)
    seed_sales(con)

    con.close()
    print("\n✓ Bronze layer pipeline finished.")

if __name__ == "__main__":
    main()
