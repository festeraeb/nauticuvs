//! VRT-aware tile slicer: slices from a multi-source VRT stack.
//!
//! This is the "Master Stack" slicer — it takes a VRT dataset (which may
//! contain Sentinel-2 at 10m, Landsat at 30m, etc.) and slices it into
//! tiles with unified coordinate anchors across all sources.
//!
//! Each tile contains pixels from ALL bands in the VRT stack, resampled
//! to the target resolution.

use std::fs;
use std::path::PathBuf;

use anyhow::{Context, Result};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tracing::{debug, info};

use crate::io::geotiff::GeoTransform;
use crate::io::vrt::{ResampleMethod, VrtDataset, VrtNormalizer};
use crate::spec::delegate::DelegateTarget;
use crate::spec::mission::MissionSpec;
use crate::tiles::anchor::{AnchorCalculator, TileAnchor};

/// A sliced tile from a VRT stack with multi-source band data.
#[derive(Debug)]
pub struct VrtSlicedTile {
    /// Raw pixel bytes from ALL bands in the VRT stack, interleaved
    /// Shape: [virtual_band, row, col]
    pub pixels: Vec<Vec<Vec<u8>>>,

    /// Baked coordinate anchor for this tile
    pub anchor: TileAnchor,

    /// Which hardware delegate should process this tile
    pub delegate: DelegateTarget,

    /// Tile grid position (col, row)
    pub grid_pos: (usize, usize),

    /// Band names for this tile (from VRT sources)
    pub band_names: Vec<String>,

    /// Source providers for each band
    pub providers: Vec<String>,
}

/// Manifest for a VRT slicing run.
#[derive(Debug, Serialize, Deserialize)]
pub struct VrtTileManifest {
    pub mission_id: String,
    pub vrt_path: String,
    pub tile_count: usize,
    pub tile_size: usize,
    pub band_count: usize,
    pub band_names: Vec<String>,
    pub tiles: Vec<VrtTileManifestEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct VrtTileManifestEntry {
    pub tile_id: String,
    pub grid_col: usize,
    pub grid_row: usize,
    pub origin_lat: f64,
    pub origin_lon: f64,
    pub delegate: String,
    pub pixel_file: String,
    pub sidecar_file: String,
}

/// VRT-aware tile slicer: chops a multi-source VRT stack into tiles.
pub struct VrtTileSlicer {
    pub vrt: VrtDataset,
    pub tile_size: usize,
    pub output_dir: PathBuf,
    pub provider: String,
    pub band_names: Vec<String>,
}

impl VrtTileSlicer {
    pub fn new(
        vrt: VrtDataset,
        tile_size: usize,
        output_dir: PathBuf,
        provider: String,
        band_names: Vec<String>,
    ) -> Self {
        Self {
            vrt,
            tile_size,
            output_dir,
            provider,
            band_names,
        }
    }

    /// Build a VrtTileSlicer from a VRT XML file path.
    pub fn from_vrt_file<P: AsRef<std::path::Path>>(
        vrt_path: P,
        tile_size: usize,
        output_dir: PathBuf,
        provider: String,
        band_names: Vec<String>,
    ) -> Result<Self> {
        let vrt = VrtDataset::from_file(&vrt_path)
            .context("failed to parse VRT file")?;
        Ok(Self::new(vrt, tile_size, output_dir, provider, band_names))
    }

    /// Slice the entire VRT stack into tiles and write to disk.
    ///
    /// Uses Rayon for parallel tile processing. Each tile reads from
    /// all VRT source bands with automatic resampling.
    pub fn slice_all(&self, mission: Option<&MissionSpec>) -> Result<VrtTileManifest> {
        let tiles_dir = self.output_dir.join("tiles");
        fs::create_dir_all(&tiles_dir).context("failed to create tiles dir")?;

        let tile_cols = self.vrt.width.div_ceil(self.tile_size);
        let tile_rows = self.vrt.height.div_ceil(self.tile_size);
        let anchor_calc = AnchorCalculator::new(self.vrt.geo_transform);

        info!(
            "VRT slicing {}x{} → {}x{} tiles ({}x{} px each, {} bands)",
            self.vrt.width,
            self.vrt.height,
            tile_cols,
            tile_rows,
            self.tile_size,
            self.tile_size,
            self.vrt.bands.len(),
        );

        // Parallel tile generation
        let tiles: Vec<VrtSlicedTile> = (0..tile_rows)
            .into_par_iter()
            .flat_map_iter(move |ty| {
                (0..tile_cols).filter_map(move |tx| {
                    self.slice_tile(tx, ty, &anchor_calc, mission)
                        .ok()
                })
            })
            .collect();

        // Write tiles to disk
        let mut entries = Vec::new();
        for tile in &tiles {
            let tile_id = &tile.anchor.tile_id;
            let pixel_file = tiles_dir.join(format!("{tile_id}.bin"));
            let sidecar_file = tiles_dir.join(format!("{tile_id}.json"));

            // Write pixels: [band][row][col] → flat interleaved bytes
            let mut flat_pixels = Vec::new();
            for band in &tile.pixels {
                for row in band {
                    flat_pixels.extend_from_slice(row);
                }
            }
            fs::write(&pixel_file, &flat_pixels).context("failed to write pixel file")?;

            let sidecar_json = serde_json::to_string_pretty(&tile.anchor)
                .context("failed to serialize sidecar")?;
            fs::write(&sidecar_file, sidecar_json).context("failed to write sidecar")?;

            entries.push(VrtTileManifestEntry {
                tile_id: tile_id.clone(),
                grid_col: tile.grid_pos.0,
                grid_row: tile.grid_pos.1,
                origin_lat: tile.anchor.origin.y,
                origin_lon: tile.anchor.origin.x,
                delegate: format!("{:?}", tile.delegate),
                pixel_file: pixel_file.file_name().unwrap().to_string_lossy().to_string(),
                sidecar_file: sidecar_file.file_name().unwrap().to_string_lossy().to_string(),
            });
        }

        let manifest = VrtTileManifest {
            mission_id: mission.map(|m| m.mission_id.clone()).unwrap_or_default(),
            vrt_path: self.vrt.vrt_path.display().to_string(),
            tile_count: entries.len(),
            tile_size: self.tile_size,
            band_count: self.vrt.bands.len(),
            band_names: self.vrt.bands.iter().map(|b| b.band_name.clone()).collect(),
            tiles: entries,
        };

        // Write manifest
        let manifest_path = self.output_dir.join("manifest.json");
        let manifest_json = serde_json::to_string_pretty(&manifest)?;
        fs::write(&manifest_path, manifest_json).context("failed to write manifest")?;

        info!("VRT sliced {} tiles → {:?}", manifest.tile_count, self.output_dir);
        Ok(manifest)
    }

    /// Slice a single tile at grid position (tx, ty).
    fn slice_tile(
        &self,
        tx: usize,
        ty: usize,
        anchor_calc: &AnchorCalculator,
        mission: Option<&MissionSpec>,
    ) -> Result<VrtSlicedTile> {
        let x_off = (tx * self.tile_size) as u32;
        let y_off = (ty * self.tile_size) as u32;
        let x_size = (self.tile_size as u32).min(self.vrt.width as u32 - x_off);
        let y_size = (self.tile_size as u32).min(self.vrt.height as u32 - y_off);

        // Read from VRT (auto-resamples all sources to target resolution)
        let pixels = self
            .vrt
            .read_window(x_off, y_off, x_size, y_size)
            .with_context(|| format!("failed to read VRT window at ({tx},{ty})"))?;

        // Hash the flattened pixels for a unique tile ID
        let mut hasher = Sha256::new();
        for band in &pixels {
            for row in band {
                hasher.update(row);
            }
        }
        let tile_id = format!("{:x}", hasher.finalize())[..16].to_string();

        // Bake the coordinate anchor
        let mut anchor = anchor_calc.calc(tx, ty, self.tile_size);
        anchor.tile_id = tile_id.clone();
        anchor.source_path = self.vrt.vrt_path.display().to_string();
        anchor.bands = self.vrt.bands.iter().map(|b| b.band_num as u16).collect();
        anchor.provider = self.provider.clone();

        // Determine delegate from mission spec
        let delegate = mission
            .and_then(|m| m.resolve_delegate(tx, ty, anchor.origin.y, anchor.origin.x))
            .unwrap_or(DelegateTarget::default());

        debug!(
            "VRT Tile ({tx},{ty}): id={tile_id} delegate={:?}",
            delegate
        );

        Ok(VrtSlicedTile {
            pixels,
            anchor,
            delegate,
            grid_pos: (tx, ty),
            band_names: self.vrt.bands.iter().map(|b| b.band_name.clone()).collect(),
            providers: self.vrt.bands.iter().map(|b| b.provider.clone()).collect(),
        })
    }
}

/// Helper to build a VRT stack from mission spec bands and source files.
pub fn build_vrt_from_mission(
    mission: &MissionSpec,
    source_files: &[(PathBuf, String, String)], // (path, band_name, provider)
    target_resolution: f64,
    output_vrt_path: &PathBuf,
) -> Result<VrtDataset> {
    let normalizer = VrtNormalizer::new(target_resolution, "EPSG:4326".into());

    // Compute pixel size in degrees from target_resolution (meters) at the mission center lat.
    // For Lake Michigan (~45°N): 10m ≈ 0.0001°, 30m ≈ 0.00027°.
    let center_lat = (mission.search_params.bounds[0] + mission.search_params.bounds[2]) / 2.0;
    let pixel_deg = target_resolution / (111_320.0 * center_lat.to_radians().cos().max(0.001));

    // Use mission bounds for geo transform
    let gt: GeoTransform = [
        mission.search_params.bounds[3], // west
        pixel_deg,
        0.0,
        mission.search_params.bounds[0], // north
        0.0,
        -pixel_deg,
    ];

    // Determine resampling: if native res != target, use bilinear
    let specs: Vec<(PathBuf, usize, String, String, ResampleMethod)> = source_files
        .iter()
        .enumerate()
        .map(|(i, (path, band_name, provider))| {
            // Estimate native resolution from provider name
            let native_res = if provider.contains("landsat") || provider.contains("landsat") {
                30.0
            } else if provider.contains("sentinel") {
                10.0
            } else {
                target_resolution
            };

            let resampling = if (native_res - target_resolution).abs() > 1.0 {
                ResampleMethod::Bilinear
            } else {
                ResampleMethod::NearestNeighbor
            };

            (path.clone(), i, band_name.clone(), provider.clone(), resampling)
        })
        .collect();

    let xml = normalizer.build_from_files(&specs, gt)?;
    fs::write(output_vrt_path, &xml).context("failed to write VRT file")?;

    VrtDataset::from_file(output_vrt_path).context("failed to parse generated VRT")
}

// ─────────────────────────────────────────────────────────────────────────────
// Post-slice quality: drift measurement + tile overlap detection
// ─────────────────────────────────────────────────────────────────────────────

/// Georeference drift report for a single tile.
#[derive(Debug, Serialize, Deserialize)]
pub struct TileDrift {
    pub tile_id: String,
    pub grid_col: usize,
    pub grid_row: usize,
    /// Expected origin lat (from VRT geo transform)
    pub expected_lat: f64,
    /// Expected origin lon (from VRT geo transform)
    pub expected_lon: f64,
    /// Actual origin lat (from sidecar anchor)
    pub actual_lat: f64,
    /// Actual origin lon (from sidecar anchor)
    pub actual_lon: f64,
    /// Drift distance in meters
    pub drift_m: f64,
}

/// Tile overlap report.
#[derive(Debug, Serialize, Deserialize)]
pub struct TileOverlap {
    pub tile_a_id: String,
    pub tile_b_id: String,
    pub tile_a_pos: (usize, usize),
    pub tile_b_pos: (usize, usize),
    /// Overlap area in square meters (0 = no overlap)
    pub overlap_m2: f64,
    /// Whether overlap exceeds expected tile boundary padding
    pub excessive: bool,
}

/// Post-slice quality report.
#[derive(Debug, Serialize, Deserialize)]
pub struct VrtQualityReport {
    pub tile_count: usize,
    pub drift_samples: Vec<TileDrift>,
    pub drift_stats: DriftStats,
    pub overlaps: Vec<TileOverlap>,
    pub excessive_overlap_count: usize,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DriftStats {
    pub mean_m: f64,
    pub max_m: f64,
    pub rms_m: f64,
    pub p95_m: f64,
    pub pass_threshold: f64,
    pub passed: bool,
}

impl VrtTileSlicer {
    /// Run post-slice drift measurement.
    ///
    /// Compares each tile's baked anchor coordinates against the expected
    /// position from the VRT geo transform. Returns drift in meters.
    pub fn measure_drift(
        &self,
        manifest: &VrtTileManifest,
        _pass_threshold_m: f64,
    ) -> Vec<TileDrift> {
        let gt = self.vrt.geo_transform;
        let tile_size = self.tile_size as f64;

        manifest
            .tiles
            .iter()
            .map(|entry| {
                // Expected position from VRT grid
                let expected_lon =
                    gt[0] + (entry.grid_col as f64 * tile_size * gt[1]);
                let expected_lat =
                    gt[3] + (entry.grid_row as f64 * tile_size * gt[5]);

                // Actual position from sidecar anchor
                let actual_lat = entry.origin_lat;
                let actual_lon = entry.origin_lon;

                // Haversine distance in meters
                let drift_m = haversine_meters(
                    expected_lat, expected_lon, actual_lat, actual_lon,
                );

                TileDrift {
                    tile_id: entry.tile_id.clone(),
                    grid_col: entry.grid_col,
                    grid_row: entry.grid_row,
                    expected_lat,
                    expected_lon,
                    actual_lat,
                    actual_lon,
                    drift_m,
                }
            })
            .collect()
    }

    /// Detect tile overlaps by comparing bounding boxes.
    ///
    /// Tiles at adjacent grid positions should NOT overlap beyond their
    /// expected boundaries. Excessive overlap indicates misalignment.
    pub fn detect_overlaps(
        &self,
        manifest: &VrtTileManifest,
    ) -> Vec<TileOverlap> {
        let mut overlaps = Vec::new();
        let tile_size = self.tile_size as f64;
        let gt = self.vrt.geo_transform;

        // Build a map of (col, row) → entry for neighbor lookups
        let mut tile_map: std::collections::HashMap<(usize, usize), &VrtTileManifestEntry> =
            std::collections::HashMap::new();
        for entry in &manifest.tiles {
            tile_map.insert((entry.grid_col, entry.grid_row), entry);
        }

        for entry in &manifest.tiles {
            let col = entry.grid_col;
            let row = entry.grid_row;

            // Check east neighbor
            if let Some(east) = tile_map.get(&(col + 1, row)) {
                // Expected: this tile ends where east tile begins
                // If actual boundaries overlap, report it
                let this_east_lon = entry.origin_lon + tile_size * gt[1].abs();
                let east_lon = east.origin_lon;
                let overlap_lon = this_east_lon - east_lon;

                // gt[5] is negative for north-up: origin_lat + tile_size * gt[5] moves south
                let this_south_lat = entry.origin_lat + tile_size * gt[5];
                let east_south_lat = east.origin_lat + tile_size * gt[5];
                let overlap_lat = this_south_lat.min(east_south_lat) - entry.origin_lat.max(east.origin_lat);

                if overlap_lon > 0.0 && overlap_lat > 0.0 {
                    // Convert degree overlap to meters
                    let mid_lat = (entry.origin_lat + east.origin_lat) / 2.0;
                    let lon_m_per_deg = mid_lat.to_radians().cos() * 111_320.0;
                    let lat_m_per_deg = 111_320.0;
                    let overlap_m2 = overlap_lon * lon_m_per_deg * overlap_lat * lat_m_per_deg;

                    // Expected overlap should be ~0 for adjacent non-overlapping tiles
                    let excessive = overlap_m2 > (tile_size * 0.1).powi(2); // >10% tile area

                    overlaps.push(TileOverlap {
                        tile_a_id: entry.tile_id.clone(),
                        tile_b_id: east.tile_id.clone(),
                        tile_a_pos: (col, row),
                        tile_b_pos: (col + 1, row),
                        overlap_m2,
                        excessive,
                    });
                }
            }

            // Check south neighbor
            if let Some(south) = tile_map.get(&(col, row + 1)) {
                // gt[5] is negative for north-up: origin_lat + tile_size * gt[5] moves south
                let this_south_lat = entry.origin_lat + tile_size * gt[5];
                let south_lat = south.origin_lat;
                let overlap_lat = this_south_lat - south_lat;

                let this_east_lon = entry.origin_lon + tile_size * gt[1].abs();
                let south_east_lon = south.origin_lon + tile_size * gt[1].abs();
                let overlap_lon = this_east_lon.min(south_east_lon) - entry.origin_lon.max(south.origin_lon);

                if overlap_lon > 0.0 && overlap_lat > 0.0 {
                    let mid_lat = (entry.origin_lat + south.origin_lat) / 2.0;
                    let lon_m_per_deg = mid_lat.to_radians().cos() * 111_320.0;
                    let lat_m_per_deg = 111_320.0;
                    let overlap_m2 = overlap_lon * lon_m_per_deg * overlap_lat * lat_m_per_deg;
                    let excessive = overlap_m2 > (tile_size * 0.1).powi(2);

                    overlaps.push(TileOverlap {
                        tile_a_id: entry.tile_id.clone(),
                        tile_b_id: south.tile_id.clone(),
                        tile_a_pos: (col, row),
                        tile_b_pos: (col, row + 1),
                        overlap_m2,
                        excessive,
                    });
                }
            }
        }

        overlaps
    }

    /// Run full quality report: drift + overlap.
    pub fn quality_report(
        &self,
        manifest: &VrtTileManifest,
        drift_threshold_m: f64,
    ) -> VrtQualityReport {
        let drift_samples = self.measure_drift(manifest, drift_threshold_m);
        let overlaps = self.detect_overlaps(manifest);

        let drift_values: Vec<f64> = drift_samples.iter().map(|d| d.drift_m).collect();
        let mut sorted = drift_values.clone();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        let mean_m = if sorted.is_empty() {
            0.0
        } else {
            sorted.iter().sum::<f64>() / sorted.len() as f64
        };
        let max_m = sorted.last().copied().unwrap_or(0.0);
        let rms_m = if sorted.is_empty() {
            0.0
        } else {
            (sorted.iter().map(|v| v * v).sum::<f64>() / sorted.len() as f64).sqrt()
        };
        let p95_idx = (sorted.len() as f64 * 0.95).ceil() as usize;
        let p95_m = sorted.get(p95_idx.min(sorted.len() - 1)).copied().unwrap_or(0.0);
        let excessive_count = overlaps.iter().filter(|o| o.excessive).count();

        VrtQualityReport {
            tile_count: manifest.tile_count,
            drift_samples,
            drift_stats: DriftStats {
                mean_m,
                max_m,
                rms_m,
                p95_m,
                pass_threshold: drift_threshold_m,
                passed: max_m <= drift_threshold_m,
            },
            overlaps,
            excessive_overlap_count: excessive_count,
        }
    }
}

/// Haversine distance between two lat/lon points in meters.
fn haversine_meters(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    const R: f64 = 6_371_000.0; // Earth radius in meters
    let dlat = (lat2 - lat1).to_radians();
    let dlon = (lon2 - lon1).to_radians();
    let a = (dlat / 2.0).sin().powi(2)
        + lat1.to_radians().cos() * lat2.to_radians().cos() * (dlon / 2.0).sin().powi(2);
    let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
    R * c
}

#[cfg(test)]
mod drift_tests {
    use super::*;

    #[test]
    fn test_haversine_chicago_milwaukee() {
        // Chicago to Milwaukee ≈ 150km
        let d = haversine_meters(41.8781, -87.6298, 43.0389, -87.9065);
        assert!((d - 150_000.0).abs() < 5_000.0); // within 5km
    }

    #[test]
    fn test_haversine_same_point() {
        let d = haversine_meters(43.0, -86.5, 43.0, -86.5);
        assert!(d < 0.01);
    }

    #[test]
    fn test_haversine_10m_offset() {
        // ~10m offset in latitude
        let d = haversine_meters(43.0, -86.5, 43.0 + 0.0001, -86.5);
        assert!((d - 11.0).abs() < 2.0);
    }
}
