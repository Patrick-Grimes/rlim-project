import duckdb

con = duckdb.connect("data/transit_warehouse.duckdb")
con.execute("LOAD spatial;")

print("=====================================================================")
print("                   BRONZE DATA INTEGRITY AUDIT                       ")
print("=====================================================================")

# ---------------------------------------------------------
# 1. Audit Transit Stations (bronze.gtfs_stops)
# ---------------------------------------------------------
print("\n--- 1. AUDIT: bronze.gtfs_stops ---")
station_stats = con.execute("""
    SELECT 
        COUNT(*) AS total_rows,
        COUNT(stop_id) AS non_null_ids,
        COUNT(DISTINCT stop_id) AS unique_ids,
        COUNT(geom) AS non_null_geoms,
        ROUND(MIN(stop_lat), 4) AS min_lat,
        ROUND(MAX(stop_lat), 4) AS max_lat,
        ROUND(MIN(stop_lon), 4) AS min_lon,
        ROUND(MAX(stop_lon), 4) AS max_lon
    FROM bronze.gtfs_stops;
""").fetchdf()
print(station_stats.to_string(index=False))

print("\nBreakdown by Transit Corridor / Line:")
line_counts = con.execute("""
    SELECT line_name, COUNT(*) AS station_count
    FROM bronze.gtfs_stops
    GROUP BY line_name
    ORDER BY station_count DESC;
""").fetchdf()
print(line_counts.to_string(index=False))

print("\nSample Stations Across Corridors:")
station_sample = con.execute("""
    SELECT stop_id, stop_name, line_name, ROUND(stop_lat, 4) AS lat, ROUND(stop_lon, 4) AS lon
    FROM bronze.gtfs_stops
    QUALIFY ROW_NUMBER() OVER (PARTITION BY line_name ORDER BY stop_id) <= 3;
""").fetchdf()
print(station_sample.to_string(index=False))

# ---------------------------------------------------------
# 2. Audit Parcels (bronze.parcels)
# ---------------------------------------------------------
print("\n--- 2. AUDIT: bronze.parcels ---")
parcel_stats = con.execute("""
    SELECT 
        COUNT(*) AS total_rows,
        COUNT(parcel_id) AS non_null_pids,
        COUNT(DISTINCT parcel_id) AS unique_pids,
        COUNT(nc_pin) AS non_null_pins,
        ROUND(MIN(shape_area_sqft), 1) AS min_sqft,
        ROUND(MAX(shape_area_sqft), 1) AS max_sqft,
        ROUND(AVG(calc_acreage), 2) AS mean_acreage,
        ROUND(MEDIAN(calc_acreage), 2) AS median_acreage
    FROM bronze.parcels;
""").fetchdf()
print(parcel_stats.to_string(index=False))

print("\nParcel Geometry Validity & Coordinate Bounding Box:")
geom_audit = con.execute("""
    SELECT 
        ST_GeometryType(geom) AS geom_type,
        COUNT(*) AS count,
        SUM(CASE WHEN ST_IsValid(geom) THEN 1 ELSE 0 END) AS valid_geoms,
        ROUND(MIN(ST_YMin(geom)), 4) AS bbox_min_lat,
        ROUND(MAX(ST_YMax(geom)), 4) AS bbox_max_lat,
        ROUND(MIN(ST_XMin(geom)), 4) AS bbox_min_lon,
        ROUND(MAX(ST_XMax(geom)), 4) AS bbox_max_lon
    FROM bronze.parcels
    GROUP BY ST_GeometryType(geom);
""").fetchdf()
print(geom_audit.to_string(index=False))

print("\nSample Parcel Records:")
parcel_sample = con.execute("""
    SELECT 
        parcel_id,
        nc_pin,
        shape_area_sqft,
        calc_acreage,
        ST_GeometryType(geom) AS geom_type
    FROM bronze.parcels
    LIMIT 5;
""").fetchdf()
print(parcel_sample.to_string(index=False))

con.close()
print("\n=====================================================================")
