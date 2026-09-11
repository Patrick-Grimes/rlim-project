import duckdb
from pathlib import Path

DB_PATH = "data/transit_warehouse.duckdb"
BRONZE_DIR = Path("data/bronze")

(BRONZE_DIR / "parcels").mkdir(parents=True, exist_ok=True)
(BRONZE_DIR / "sales").mkdir(parents=True, exist_ok=True)
(BRONZE_DIR / "gtfs").mkdir(parents=True, exist_ok=True)

# 1. Connect DuckDB and Load Spatial Extension
con = duckdb.connect(DB_PATH)
con.execute("INSTALL spatial; LOAD spatial;")

print("Setting up Bronze layer in DuckDB...")

# 2. Initialize Bronze Schemas
con.execute("CREATE SCHEMA IF NOT EXISTS bronze;")

con.execute("""
CREATE OR REPLACE TABLE bronze.parcels (
    parcel_id VARCHAR PRIMARY KEY,
    owner_name VARCHAR,
    legal_desc VARCHAR,
    zoning VARCHAR,
    land_unit_val DOUBLE,
    calc_acreage DOUBLE,
    geom GEOMETRY,
    ingest_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

con.execute("""
CREATE OR REPLACE TABLE bronze.property_sales (
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

con.execute("""
CREATE OR REPLACE TABLE bronze.gtfs_stops (
    stop_id VARCHAR PRIMARY KEY,
    stop_code VARCHAR,
    stop_name VARCHAR,
    stop_lat DOUBLE,
    stop_lon DOUBLE,
    zone_id VARCHAR,
    ingest_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

# 3. Seed Initial Records
con.execute("""
INSERT INTO bronze.gtfs_stops (stop_id, stop_code, stop_name, stop_lat, stop_lon, zone_id)
VALUES 
    ('STN_DAVIDSON_01', 'RL_DAV', 'Davidson Main & Griffith', 35.4993, -80.8431, 'NORTH_MECK'),
    ('STN_CORNELIUS_01', 'RL_COR', 'Cornelius Town Center', 35.4866, -80.8601, 'NORTH_MECK'),
    ('STN_SOUTHHEND_01', 'BL_NBN', 'New Bern Station (Blue Line)', 35.2003, -80.8687, 'CENTRAL')
ON CONFLICT (stop_id) DO NOTHING;
""")

con.execute("""
INSERT INTO bronze.parcels (parcel_id, owner_name, legal_desc, zoning, land_unit_val, calc_acreage, geom)
VALUES 
    ('00123456', 'SAMPLE_OWNER_A', 'LOT 1 BLK 2 MAIN ST', 'TOD-CC', 320000.0, 0.45, ST_Point(-80.8431, 35.4993)),
    ('00123457', 'SAMPLE_OWNER_B', 'LOT 3 BLK 2 GRIFFITH ST', 'R-1', 450000.0, 0.75, ST_Point(-80.8440, 35.4988))
ON CONFLICT (parcel_id) DO NOTHING;
""")

con.execute("""
INSERT INTO bronze.property_sales (sale_id, parcel_id, sale_date, sale_price, qualified_sale_flag, deed_book, deed_page)
VALUES 
    ('SALE_2024_001', '00123456', '2024-06-15', 550000.0, 'Y', '34521', '102'),
    ('SALE_2021_092', '00123457', '2021-03-20', 420000.0, 'Y', '31200', '45')
ON CONFLICT (sale_id) DO NOTHING;
""")

print("Bronze tables seeded successfully in:", DB_PATH)

# Verify count
parcel_count = con.execute("SELECT count(*) FROM bronze.parcels;").fetchone()[0]
sales_count = con.execute("SELECT count(*) FROM bronze.property_sales;").fetchone()[0]
stops_count = con.execute("SELECT count(*) FROM bronze.gtfs_stops;").fetchone()[0]

print(f"Summary Verification -> Parcels: {parcel_count} | Sales: {sales_count} | Transit Stops: {stops_count}")

con.close()

