//! The core tile slicer: iterates over a mmap'd GeoTIFF, extracts tiles,
//! bakes coordinate anchors, and writes tile data + sidecar JSON to disk.
//!
//! Output structure:
//! ```text
//! output/
//!   tiles/
//!     tile_000_000.bin          # Raw pixel data (BIP interleaved)
//!     tile_000_000.json         # Sidecar anchor
//!     tile_001_000.bin
//!     tile_001_000.json
//!     ...
//!   manifest.json               # Index of all tiles with anchors
//! ```

use std::fs;
use std::path::PathBuf;

use anyhow::{Context, Result};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tracing::{debug, info};

use crate::io::geotiff::{GeoTransform, MmapGeoTiff};
use crate::spec::delegate::DelegateTarget;
use crate::spec::mission::MissionSpec;
use crate::tiles::anchor::{AnchorCalculator, TileAnchor};

/// A sliced tile with its raw pixel data and baked anchor.
#[derive(Debug)]
pub struct SlicedTile {
    /// Raw pixel bytes (interleaved bands, row-major: [band, row, col])
    pub pixels: Vec<u8>,

    /// Baked coordinate anchor for this tile
    pub anchor: TileAnchor,

    /// Which hardware delegate should process this tile
    pub delegate: DelegateTarget,

    /// Tile grid position (col, row)
    pub grid_pos: (usize, usize),
}

/// Manifest: index of all tiles in a slicing run.
#[derive(Debug, Serialize, Deserialize)]
pub struct TileManifest {
    pub mission_id: String,
    pub tile_count: usize,
    pub tile_size: usize,
    pub tiles: Vec<TileManifestEntry>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TileManifestEntry {
    pub tile_id: String,
    pub grid_col: usize,
    pub grid_row: usize,
    pub origin_lat: f64,
    pub origin_lon: f64,
    pub delegate: String,
    pub pixel_file: String,
    pub sidecar_file: String,
}

/// The main slicer: takes a GeoTIFF, chops it into tiles, bakes anchors.
pub struct TileSlicer {
    pub tiff: MmapGeoTiff,
    pub tile_size: usize,
    pub output_dir: PathBuf,
    pub geo_transform: GeoTransform,
    pub provider: String,
    pub bands: Vec<u16>,
}

impl TileSlicer {
    pub fn new(
        tiff: MmapGeoTiff,
        tile_size: usize,
        output_dir: PathBuf,
        geo_transform: GeoTransform,
        provider: String,
        bands: Vec<u16>,
    ) -> Self {
        Self {
            tiff,
            tile_size,
            output_dir,
            geo_transform,
            provider,
            bands,
        }
    }

    /// Slice the entire GeoTIFF into tiles and write to disk.
    ///
    /// Uses Rayon for parallel tile processing. Each tile is read from the
    /// mmap'd file, hashed for dedup, and written with a sidecar JSON.
    pub fn slice_all(&self, mission: Option<&MissionSpec>) -> Result<TileManifest> {
        let tiles_dir = self.output_dir.join("tiles");
        fs::create_dir_all(&tiles_dir).context("failed to create tiles dir")?;

        let (tile_cols, tile_rows) = self.tiff.tile_counts(self.tile_size);
        let anchor_calc = AnchorCalculator::new(self.geo_transform);
        info!(
            "Slicing {}x{} into {}x{} tiles ({}x{} px each)",
            self.tiff.width,
            self.tiff.height,
            tile_cols,
            tile_rows,
            self.tile_size,
            self.tile_size
        );

        // Parallel tile generation using flat_map_iter
        let tiles: Vec<SlicedTile> = (0..tile_rows)
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

            fs::write(&pixel_file, &tile.pixels)
                .context("failed to write pixel file")?;
            let sidecar_json = serde_json::to_string_pretty(&tile.anchor)
                .context("failed to serialize sidecar")?;
            fs::write(&sidecar_file, sidecar_json).context("failed to write sidecar")?;

            entries.push(TileManifestEntry {
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

        let manifest = TileManifest {
            mission_id: mission.map(|m| m.mission_id.clone()).unwrap_or_default(),
            tile_count: entries.len(),
            tile_size: self.tile_size,
            tiles: entries,
        };

        // Write manifest
        let manifest_path = self.output_dir.join("manifest.json");
        let manifest_json = serde_json::to_string_pretty(&manifest)?;
        fs::write(&manifest_path, manifest_json).context("failed to write manifest")?;

        info!("Sliced {} tiles → {:?}", manifest.tile_count, self.output_dir);
        Ok(manifest)
    }

    /// Slice a single tile at grid position (tx, ty).
    fn slice_tile(
        &self,
        tx: usize,
        ty: usize,
        anchor_calc: &AnchorCalculator,
        mission: Option<&MissionSpec>,
    ) -> Result<SlicedTile> {
        let x_off = (tx * self.tile_size) as u32;
        let y_off = (ty * self.tile_size) as u32;
        let x_size = (self.tile_size as u32).min(self.tiff.width - x_off);
        let y_size = (self.tile_size as u32).min(self.tiff.height - y_off);

        // Read the pixel window from mmap
        let window = self
            .tiff
            .read_window(x_off, y_off, x_size, y_size)
            .with_context(|| format!("failed to read window at ({tx},{ty})"))?;

        // Flatten to interleaved byte array: [band, row, col] → flat
        let pixels: Vec<u8> = window.iter().copied().collect();

        // Hash the pixels for a unique tile ID (dedup across providers)
        let mut hasher = Sha256::new();
        hasher.update(&pixels);
        let tile_id = format!("{:x}", hasher.finalize())[..16].to_string();

        // Bake the coordinate anchor
        let mut anchor = anchor_calc.calc(tx, ty, self.tile_size);
        anchor.tile_id = tile_id.clone();
        anchor.source_path = self.tiff.path.file_name().unwrap().to_string_lossy().to_string();
        anchor.bands = self.bands.clone();
        anchor.provider = self.provider.clone();

        // Determine delegate from mission spec
        let delegate = mission
            .and_then(|m| m.resolve_delegate(tx, ty, anchor.origin.y, anchor.origin.x))
            .unwrap_or(DelegateTarget::default());

        debug!(
            "Tile ({tx},{ty}): id={tile_id} delegate={:?}",
            delegate
        );

        Ok(SlicedTile {
            pixels,
            anchor,
            delegate,
            grid_pos: (tx, ty),
        })
    }
}
