-- CESAROPS Comprehensive Database Schema
-- Logs EVERYTHING - metadata, processing info, environmental data
-- Purpose: Capture all variables for pattern discovery

-- ============================================================================
-- SCAN RUNS - Complete processing session metadata
-- ============================================================================
CREATE TABLE IF NOT EXISTS scan_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- User-defined run info
    run_name TEXT,                    -- e.g., "run_zero_baseline", "repeatability_test_1"
    run_type TEXT,                    -- "baseline", "repeatability", "production", "test"
    
    -- Input data
    input_directory TEXT,
    tile_count INTEGER,               -- How many tiles processed
    
    -- Processing configuration
    min_confidence REAL,
    overlap_percent REAL,
    chunking_enabled BOOLEAN,
    tile_size INTEGER,                -- 512 for chunked, 3660 for full-tile
    
    -- System info
    hostname TEXT,
    cpu_cores INTEGER,
    system_ram_gb REAL,
    gpu_name TEXT,
    gpu_vendor TEXT,
    gpu_vram_gb REAL,
    
    -- Software version
    cesarops_version TEXT,
    rust_version TEXT,
    build_date TEXT,
    
    -- Timing
    start_time DATETIME,
    end_time DATETIME,
    duration_seconds REAL,
    
    -- Results summary
    total_detections INTEGER,
    high_confidence_count INTEGER,    -- score > 0.8
    medium_confidence_count INTEGER,  -- score 0.6-0.8
    low_confidence_count INTEGER,     -- score 0.5-0.6
    
    -- Notes
    notes TEXT,
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- TILES PROCESSED - Individual tile metadata
-- ============================================================================
CREATE TABLE IF NOT EXISTS tiles_processed (
    tile_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    
    -- File info
    tile_path TEXT,
    tile_prefix TEXT,                 -- e.g., "HLS.L30.T16TDN.2021182T162824.v2.0"
    file_size_bytes INTEGER,
    
    -- Satellite/acquisition info
    satellite_type TEXT,              -- "L30" (Landsat-8), "S30" (Sentinel-2)
    acquisition_date TEXT,            -- e.g., "2021-07-01"
    acquisition_time TEXT,            -- e.g., "16:28:24"
    mgrs_grid TEXT,                   -- e.g., "T16TDN"
    path_row TEXT,                    -- Landsat WRS-2 path/row
    
    -- Solar geometry (CALCULATED from acquisition time/location)
    sun_elevation_deg REAL,           -- Sun angle above horizon
    sun_azimuth_deg REAL,             -- Sun compass direction
    solar_zenith_deg REAL,            -- 90 - elevation
    
    -- Original tile properties
    original_width_px INTEGER,
    original_height_px INTEGER,
    pixel_size_meters REAL,           -- Ground sample distance
    
    -- Geospatial
    geotransform TEXT,                -- JSON array [gt0,gt1,gt2,gt3,gt4,gt5]
    crs TEXT,                         -- e.g., "EPSG:32616"
    utm_zone INTEGER,
    northern_hemisphere BOOLEAN,
    
    -- Band info
    bands_processed TEXT,             -- JSON array ["B04","B05","B10","B11"]
    band_min_dn TEXT,                 -- JSON object {"B04": 1234, "B05": 2345}
    band_max_dn TEXT,
    band_mean_dn TEXT,
    band_stddev_dn TEXT,
    
    -- Data quality
    nodata_pixel_count INTEGER,
    nodata_percent REAL,
    cloud_cover_percent REAL,         -- If available from metadata
    
    -- Processing metrics
    load_time_seconds REAL,
    gpu_upload_time_seconds REAL,
    gpu_compute_time_seconds REAL,
    total_processing_time_seconds REAL,
    
    -- GPU state during processing
    gpu_temperature_c REAL,
    gpu_memory_used_mb REAL,
    gpu_utilization_percent REAL,
    
    -- Chunking info
    was_chunked BOOLEAN,
    chunk_count INTEGER,
    chunk_size_px INTEGER,
    overlap_percent REAL,
    
    -- Anomaly stats (before filtering)
    raw_anomaly_count INTEGER,
    top_anomaly_z_score REAL,
    thermal_mean REAL,
    thermal_stddev REAL,
    thermal_valid_pixels INTEGER,
    
    -- NauticUVs curvelets processing
    nauticuvs_enabled BOOLEAN DEFAULT FALSE,
    nauticuvs_version TEXT,           -- e.g., "1.0.3"
    nauticuvs_fdct_energy_ratio REAL, -- FDCT energy ratio from nauticuvs
    nauticuvs_scales INTEGER,         -- Number of curvelet scales used
    nauticuvs_angles INTEGER,         -- Number of orientations per scale
    nauticuvs_edge_density REAL,      -- Edge density percentage
    nauticuvs_directional_strength REAL,
    nauticuvs_linear_features INTEGER,
    nauticuvs_processing_time_seconds REAL,
    
    -- Atmospheric corrections applied
    atmospheric_correction TEXT,      -- "none", "dos", "ledaps", "laads", "sen2cor"
    aerosol_optical_depth REAL,       -- AOD at 550nm
    water_vapor_content REAL,         -- g/cm²
    ozone_content REAL,               -- DU (Dobson units)
    rayleigh_correction_applied BOOLEAN,
    adjacency_correction_applied BOOLEAN,
    
    -- Water column corrections
    sun_glint_correction_applied BOOLEAN,
    sun_glint_residual REAL,          -- Remaining glint after correction
    whitecap_correction_applied BOOLEAN,
    water_surface_reflection REAL,    -- Percentage
    refraction_correction_applied BOOLEAN,
    depth_invariant_index_applied BOOLEAN,  -- Lyzenga algorithm
    
    -- Geometric/terrain corrections
    terrain_correction_applied BOOLEAN,
    brdf_correction_applied BOOLEAN,  -- Bidirectional Reflectance Distribution Function
    co_registration_rmse REAL,        -- Root mean square error in pixels
    geolocation_accuracy_m REAL,      -- Meters CE90
    
    -- Sensor artifacts
    striping_index REAL,              -- Measure of along-track striping
    dead_pixel_count INTEGER,
    compression_artifacts BOOLEAN,
    quantization_noise_estimate REAL,
    
    -- Processing artifacts
    resampling_method TEXT,           -- "nearest", "bilinear", "cubic", "lanczos"
    resampling_artifacts BOOLEAN,
    edge_effect_severity REAL,        -- 0-1 scale, 1 = severe
    boundary_discontinuity REAL,      -- Mean difference at tile boundaries
    
    -- Fuel leak / pipeline monitoring (Line 5)
    b08_nir_reflectance REAL,         -- Near-infrared "glow" detection
    b11_swir_reflectance REAL,        -- SWIR for oxidized hull detection
    b12_swir2_reflectance REAL,       -- SWIR-2 "bubble eraser"
    b02_blue_reflectance REAL,        -- Blue band for rust detection
    b12_bubble_mask_applied BOOLEAN,  -- TRUE if B12 filtering applied
    is_fuel_sheen_candidate BOOLEAN,  -- Bright in B08, dark in B12
    is_bubble_foam BOOLEAN,           -- Bright in both B08 and B12
    sar_cooccurrence BOOLEAN,         -- Sentinel-1 SAR black scar match
    pipeline_leak_confidence REAL,    -- 0.0-1.0 confidence for Line 5 monitoring
    
    FOREIGN KEY (run_id) REFERENCES scan_runs(run_id)
);

-- ============================================================================
-- RAW DETECTIONS - Every single detection, no filtering
-- ============================================================================
CREATE TABLE IF NOT EXISTS raw_detections (
    detection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    tile_id INTEGER NOT NULL,
    run_id INTEGER NOT NULL,
    
    -- Pixel location
    pixel_row INTEGER,
    pixel_col INTEGER,
    
    -- Distance from tile edge (for edge effect analysis)
    distance_from_edge_px REAL,
    is_near_edge BOOLEAN,             -- Within 50px of edge
    
    -- Geospatial coordinates
    utm_easting REAL,
    utm_northing REAL,
    utm_zone INTEGER,
    wgs84_lat REAL,
    wgs84_lon REAL,
    grid_ref TEXT,                    -- WH2K-XXXX-YYYY
    
    -- Raw sensor measurements
    aluminum_ratio REAL,              -- B08/B04 or B05/B04
    thermal_z_score REAL,             -- GPU Z-score (can be ANY value)
    thermal_brightness_temp REAL,     -- Actual brightness temperature if available
    
    -- Derived scores
    base_score REAL,                  -- (aluminum + |thermal_z|) / 2.0
    
    -- Classification (pre-filter)
    raw_classification TEXT,          -- Based on raw values
    
    -- Z-score validity flags
    is_valid_thermal_z BOOLEAN,       -- Z between 1-4
    is_valid_glint_z BOOLEAN,         -- Z between 10-65
    is_outside_ranges BOOLEAN,        -- Z <1 or >65 (potential noise)
    
    -- Filtering flags
    would_be_filtered BOOLEAN,
    filter_reason TEXT,
    
    -- Multi-pass tracking (populated in post-processing)
    cluster_id TEXT,
    pass_count INTEGER DEFAULT 1,
    
    -- Sensor detection flags
    detected_by_thermal BOOLEAN,
    detected_by_optical BOOLEAN,
    detected_by_sar BOOLEAN,
    detected_by_swot BOOLEAN,
    
    -- Target characteristics
    is_surface_only BOOLEAN,
    is_subsurface BOOLEAN,
    is_borderline BOOLEAN,
    
    -- Examination flags
    needs_examination BOOLEAN,
    examination_reason TEXT,
    examination_priority TEXT,
    
    -- Local neighborhood stats
    local_mean_thermal REAL,
    local_stddev_thermal REAL,
    local_mean_aluminum REAL,
    local_stddev_aluminum REAL,
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (tile_id) REFERENCES tiles_processed(tile_id),
    FOREIGN KEY (run_id) REFERENCES scan_runs(run_id)
);

-- ============================================================================
-- CLUSTERED TARGETS - Merged detections from multiple passes
-- ============================================================================
CREATE TABLE IF NOT EXISTS clustered_targets (
    target_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_uuid TEXT UNIQUE NOT NULL,
    
    -- Representative location (centroid)
    center_utm_easting REAL,
    center_utm_northing REAL,
    center_wgs84_lat REAL,
    center_wgs84_lon REAL,
    grid_ref TEXT,
    
    -- Cluster statistics
    detection_count INTEGER,
    pass_count INTEGER,
    sensor_lock_count INTEGER,
    
    -- Lock level
    lock_level TEXT,                  -- 'single', 'double', 'triple', 'quad'
    
    -- Confidence scoring
    base_confidence REAL,
    multi_pass_boost REAL,
    multi_sensor_boost REAL,
    final_confidence REAL,
    
    -- Spatial spread
    max_spread_meters REAL,
    avg_spread_meters REAL,
    spread_classification TEXT,       -- 'tight', 'moderate', 'loose'
    
    -- Target characteristics
    is_consistent_across_passes BOOLEAN,
    is_surface_target BOOLEAN,
    is_subsurface_target BOOLEAN,
    is_transient BOOLEAN,
    
    -- Classification
    primary_classification TEXT,
    
    -- Examination status
    needs_examination BOOLEAN,
    examination_reason TEXT,
    ground_truth_verified BOOLEAN,
    ground_truth_type TEXT,
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- ENVIRONMENTAL CONDITIONS - External data for correlation
-- ============================================================================
CREATE TABLE IF NOT EXISTS environmental_conditions (
    condition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Date/time
    observation_date TEXT,
    observation_time TEXT,
    
    -- Location reference
    lat REAL,
    lon REAL,
    location_name TEXT,               -- e.g., "NDBC Buoy 45007"
    
    -- Weather
    air_temperature_c REAL,
    water_temperature_c REAL,
    wind_speed_ms REAL,
    wind_direction_deg REAL,
    cloud_cover_percent REAL,
    
    -- Water conditions
    water_level_m REAL,
    water_level_status TEXT,          -- 'high', 'normal', 'low'
    wave_height_m REAL,
    secchi_depth_m REAL,              -- Water clarity
    
    -- Solar
    sun_elevation_deg REAL,
    
    -- Source
    data_source TEXT,                 -- 'NDBC', 'NOAA', 'GLERL'
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_detections_location ON raw_detections(utm_easting, utm_northing);
CREATE INDEX IF NOT EXISTS idx_detections_tile ON raw_detections(tile_id);
CREATE INDEX IF NOT EXISTS idx_detections_thermal_z ON raw_detections(thermal_z_score);
CREATE INDEX IF NOT EXISTS idx_detections_score ON raw_detections(base_score);
CREATE INDEX IF NOT EXISTS idx_detections_edge ON raw_detections(is_near_edge);
CREATE INDEX IF NOT EXISTS idx_tiles_acquisition ON tiles_processed(acquisition_date);
CREATE INDEX IF NOT EXISTS idx_tiles_sun_elevation ON tiles_processed(sun_elevation_deg);
CREATE INDEX IF NOT EXISTS idx_clusters_location ON clustered_targets(center_utm_easting, center_utm_northing);

-- ============================================================================
-- VIEWS FOR ANALYSIS
-- ============================================================================

-- View: Detections with full tile context
CREATE VIEW IF NOT EXISTS v_detections_full AS
SELECT 
    rd.detection_id,
    rd.pixel_row,
    rd.pixel_col,
    rd.distance_from_edge_px,
    rd.is_near_edge,
    rd.wgs84_lat,
    rd.wgs84_lon,
    rd.grid_ref,
    rd.aluminum_ratio,
    rd.thermal_z_score,
    rd.base_score,
    rd.would_be_filtered,
    rd.filter_reason,
    tp.acquisition_date,
    tp.acquisition_time,
    tp.sun_elevation_deg,
    tp.sun_azimuth_deg,
    tp.satellite_type,
    tp.bands_processed,
    tp.was_chunked,
    tp.chunk_count,
    tp.gpu_temperature_c,
    sr.run_name,
    sr.run_type
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
JOIN scan_runs sr ON rd.run_id = sr.run_id;

-- View: Detection density by sun elevation
CREATE VIEW IF NOT EXISTS v_sun_angle_analysis AS
SELECT 
    CASE 
        WHEN tp.sun_elevation_deg < 30 THEN 'Low (<30°)'
        WHEN tp.sun_elevation_deg < 60 THEN 'Medium (30-60°)'
        ELSE 'High (>60°)'
    END as sun_angle_category,
    COUNT(rd.detection_id) as detection_count,
    AVG(rd.base_score) as avg_score,
    AVG(rd.thermal_z_score) as avg_thermal_z
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY sun_angle_category;

-- View: Edge effect analysis
CREATE VIEW IF NOT EXISTS v_edge_effect_analysis AS
SELECT 
    rd.is_near_edge,
    COUNT(*) as detection_count,
    AVG(rd.base_score) as avg_score,
    AVG(rd.thermal_z_score) as avg_thermal_z,
    COUNT(CASE WHEN rd.would_be_filtered THEN 1 END) as filtered_count
FROM raw_detections rd
GROUP BY rd.is_near_edge;

-- View: Chunked vs full-tile comparison
CREATE VIEW IF NOT EXISTS v_chunking_comparison AS
SELECT 
    tp.was_chunked,
    tp.chunk_count,
    COUNT(rd.detection_id) as total_detections,
    AVG(rd.base_score) as avg_score,
    AVG(rd.distance_from_edge_px) as avg_distance_from_edge
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY tp.was_chunked, tp.chunk_count;

-- View: Atmospheric correction effectiveness
CREATE VIEW IF NOT EXISTS v_atmospheric_correction_analysis AS
SELECT 
    tp.atmospheric_correction,
    COUNT(rd.detection_id) as detection_count,
    AVG(rd.base_score) as avg_score,
    AVG(rd.thermal_z_score) as avg_thermal_z,
    AVG(tp.aerosol_optical_depth) as avg_aod,
    AVG(tp.water_vapor_content) as avg_water_vapor
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY tp.atmospheric_correction;

-- View: Glint correction impact
CREATE VIEW IF NOT EXISTS v_glint_correction_analysis AS
SELECT 
    tp.sun_glint_correction_applied,
    tp.sun_glint_residual,
    COUNT(rd.detection_id) as detection_count,
    SUM(CASE WHEN rd.classification LIKE '%ALUMINUM%' THEN 1 ELSE 0 END) as aluminum_detections,
    AVG(rd.aluminum_ratio) as avg_aluminum_ratio
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY tp.sun_glint_correction_applied, 
         CASE WHEN tp.sun_glint_residual < 0.1 THEN 'Low (<0.1)'
              WHEN tp.sun_glint_residual < 0.3 THEN 'Medium (0.1-0.3)'
              ELSE 'High (>0.3)' END;

-- View: Edge effects from chunking
CREATE VIEW IF NOT EXISTS v_edge_effects_analysis AS
SELECT 
    tp.was_chunked,
    rd.is_near_edge,
    COUNT(rd.detection_id) as detection_count,
    AVG(rd.base_score) as avg_score,
    AVG(rd.thermal_z_score) as avg_thermal_z,
    AVG(tp.edge_effect_severity) as avg_edge_severity,
    AVG(tp.boundary_discontinuity) as avg_boundary_discontinuity
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY tp.was_chunked, rd.is_near_edge;

-- View: NauticUVs curvelets impact
CREATE VIEW IF NOT EXISTS v_nauticuvs_impact_analysis AS
SELECT 
    tp.nauticuvs_enabled,
    tp.nauticuvs_fdct_energy_ratio,
    COUNT(rd.detection_id) as detection_count,
    AVG(rd.base_score) as avg_score,
    SUM(CASE WHEN rd.classification LIKE '%STEEL%' OR rd.classification LIKE '%ALUMINUM%' THEN 1 ELSE 0 END) as high_confidence_detections,
    AVG(tp.nauticuvs_edge_density) as avg_edge_density,
    AVG(tp.nauticuvs_linear_features) as avg_linear_features
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY tp.nauticuvs_enabled,
         CASE WHEN tp.nauticuvs_fdct_energy_ratio < 0.3 THEN 'Low'
              WHEN tp.nauticuvs_fdct_energy_ratio < 0.7 THEN 'Medium'
              ELSE 'High' END;

-- View: Sensor artifact correlation
CREATE VIEW IF NOT EXISTS v_sensor_artifact_analysis AS
SELECT 
    CASE WHEN tp.striping_index > 0.1 THEN 'High striping'
         WHEN tp.striping_index > 0.05 THEN 'Medium striping'
         ELSE 'Low/No striping' END as striping_level,
    CASE WHEN tp.dead_pixel_count > 100 THEN 'Many dead pixels'
         WHEN tp.dead_pixel_count > 10 THEN 'Some dead pixels'
         ELSE 'Few/No dead pixels' END as dead_pixel_level,
    COUNT(rd.detection_id) as detection_count,
    AVG(rd.base_score) as avg_score,
    AVG(tp.co_registration_rmse) as avg_coreg_rmse
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY striping_level, dead_pixel_level;

-- View: Refraction/water column effects
CREATE VIEW IF NOT EXISTS v_water_column_analysis AS
SELECT 
    tp.refraction_correction_applied,
    tp.depth_invariant_index_applied,
    tp.water_surface_reflection,
    COUNT(rd.detection_id) as detection_count,
    AVG(rd.thermal_z_score) as avg_thermal_z,
    AVG(rd.aluminum_ratio) as avg_aluminum_ratio
FROM raw_detections rd
JOIN tiles_processed tp ON rd.tile_id = tp.tile_id
GROUP BY tp.refraction_correction_applied, 
         tp.depth_invariant_index_applied,
         CASE WHEN tp.water_surface_reflection < 5 THEN 'Low (<5%)'
              WHEN tp.water_surface_reflection < 15 THEN 'Medium (5-15%)'
              ELSE 'High (>15%)' END;
