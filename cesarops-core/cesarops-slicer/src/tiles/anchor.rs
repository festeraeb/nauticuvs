//! Tile anchor coordinate system.
//!
//! Each tile gets an "anchor" — its top-left lat/lon calculated ONCE during slicing.
//! This eliminates drift: the scanner adds local pixel offsets to the anchor
//! instead of re-projecting every frame.
//!
//! The anchor is serialized to a sidecar JSON alongside each tile.

use geo_types::Coord;
use serde::{Deserialize, Serialize};

use crate::io::geotiff::GeoTransform;

/// A baked coordinate anchor for a single tile.
///
/// This is the "source of truth" for positioning. Once calculated,
/// the tile's pixels are a local coordinate system offset from this anchor.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TileAnchor {
    /// Unique tile ID (SHA-256 hash of tile pixels for dedup)
    pub tile_id: String,

    /// Tile origin (top-left) in WGS84 lat/lon
    pub origin: Coord<f64>,

    /// Pixel resolution in degrees (lat per pixel, lon per pixel)
    pub pixel_resolution: Coord<f64>,

    /// Tile dimensions in pixels (width, height)
    pub tile_size: (u32, u32),

    /// Source file path (relative to the VRT root)
    pub source_path: String,

    /// Band indices included in this tile (e.g., [2,3,4] for RGB)
    pub bands: Vec<u16>,

    /// Timestamp of the source satellite pass
    pub acquisition_time: Option<String>,

    /// Provider name (e.g., "sentinel-2a", "landsat-9", "planet-sky")
    pub provider: String,

    /// Native CRS of the source (e.g., "EPSG:4326", "EPSG:32617")
    pub native_crs: String,
}

impl TileAnchor {
    /// Convert a local pixel offset within the tile to global lat/lon.
    ///
    /// This is the "zero-drift" lookup: no reprojection needed,
    /// just add the offset to the baked anchor.
    pub fn pixel_to_global(&self, local_col: u32, local_row: u32) -> Coord<f64> {
        Coord {
            x: self.origin.x + (local_col as f64 * self.pixel_resolution.x),
            y: self.origin.y + (local_row as f64 * self.pixel_resolution.y),
        }
    }

    /// Calculate the global bounding box of this tile.
    pub fn bbox(&self) -> (Coord<f64>, Coord<f64>) {
        let (w, h) = self.tile_size;
        let top_left = self.origin;
        let bottom_right = Coord {
            x: self.origin.x + (w as f64 * self.pixel_resolution.x),
            y: self.origin.y + (h as f64 * self.pixel_resolution.y),
        };
        (top_left, bottom_right)
    }
}

/// Helper to compute a TileAnchor from a geotransform and tile grid position.
#[derive(Clone, Copy)]
pub struct AnchorCalculator {
    pub geo_transform: GeoTransform,
    pub pixel_resolution: f64,
}

impl AnchorCalculator {
    pub fn new(geo_transform: GeoTransform) -> Self {
        // For north-up images: pixel_resolution = abs(geo_transform[1]) for lon,
        // abs(geo_transform[5]) for lat
        let pixel_resolution = (geo_transform[1].abs() + geo_transform[5].abs()) / 2.0;
        Self {
            geo_transform,
            pixel_resolution,
        }
    }

    /// Calculate the anchor for a tile at grid position (tile_col, tile_row).
    pub fn calc(&self, tile_col: usize, tile_row: usize, tile_size: usize) -> TileAnchor {
        let origin_lat =
            self.geo_transform[3] + (tile_row as f64 * tile_size as f64 * self.geo_transform[5]);
        let origin_lon =
            self.geo_transform[0] + (tile_col as f64 * tile_size as f64 * self.geo_transform[1]);

        TileAnchor {
            tile_id: String::new(), // Filled in by slicer after hash
            origin: Coord {
                x: origin_lon,
                y: origin_lat,
            },
            pixel_resolution: Coord {
                x: self.geo_transform[1],
                y: self.geo_transform[5],
            },
            tile_size: (tile_size as u32, tile_size as u32),
            source_path: String::new(), // Filled in by slicer
            bands: vec![],              // Filled in by slicer
            acquisition_time: None,     // Filled in from metadata
            provider: String::from("unknown"),
            native_crs: String::from("EPSG:4326"),
        }
    }
}
