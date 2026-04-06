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
use crate::io::vrt::{ResampleMethod, VrtDataset, VrtSource};
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

    // Use mission bounds for geo transform
    let gt: GeoTransform = [
        mission.search_params.bounds[3], // west
        0.0001,                          // ~10m
        0.0,
        mission.search_params.bounds[0], // north
        0.0,
        -0.0001,
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
