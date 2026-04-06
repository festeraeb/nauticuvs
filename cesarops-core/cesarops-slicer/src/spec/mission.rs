//! Mission spec parsing: Qwen's JSON output → Rust module routing.
//!
//! This is the "Knob Turner" interface. Qwen outputs a JSON spec
//! with mission parameters, sensor bands, filter configs, and
//! target coordinates. We parse it and route tiles to the right
//! hardware delegates (Coral TPU, P1000 GPU, CPU).
//!
//! ## JSON Schema (what Qwen should output)
//!
//! ```json
//! {
//!   "mission_id": "LAKE_MI_MONSTER_001",
//!   "target_ref": "Andoste_Vicinity",
//!   "search_params": {
//!     "bounds": [42.80, -86.30, 43.10, -86.60],
//!     "depth_target_feet": 450,
//!     "expected_length_meters": 100
//!   },
//!   "modules": [
//!     {
//!       "id": "SiltMasker_B4_B3",
//!       "mode": "TRANSPARENCY_HOLE",
//!       "delegate": "CORAL_TPU_INT8",
//!       "params": { "silt_coeff": 0.85 },
//!       "roi": { "lat": 42.95, "lon": -86.45, "radius_km": 5.0 }
//!     }
//!   ],
//!   "output_strategy": {
//!     "sync_node": "PI_JANITOR_SYNCTHING",
//!     "storage": "SANDISK_G_ARMOR_1TB",
//!     "frontend": "TAURI_P51_LAPTOP"
//!   }
//! }
//! ```

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;

use crate::spec::delegate::DelegateTarget;

/// Top-level mission specification from Qwen.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MissionSpec {
    /// Unique mission identifier (e.g., "LAKE_MI_MONSTER_001")
    pub mission_id: String,

    /// Human-readable target reference
    pub target_ref: String,

    /// Search area and depth parameters
    pub search_params: SearchParams,

    /// Processing modules to run
    pub modules: Vec<ModuleSpec>,

    /// Output and sync configuration
    pub output_strategy: OutputStrategy,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchParams {
    /// Bounding box: [north, east, south, west]
    pub bounds: [f64; 4],

    /// Target depth in feet (for depth-correlated analysis)
    pub depth_target_feet: Option<u32>,

    /// Expected target length in meters (for scale filtering)
    pub expected_length_meters: Option<f64>,

    /// Tile size override (default: 1024)
    pub tile_size: Option<usize>,
}

/// A single processing module from the mission spec.
///
/// Each module maps to a Rust processing function and optionally
/// targets a specific hardware delegate.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModuleSpec {
    /// Module identifier (e.g., "SiltMasker_B4_B3")
    pub id: String,

    /// Processing mode (e.g., "TRANSPARENCY_HOLE", "ANTI_WAKE", "THERMAL_SPINE")
    pub mode: String,

    /// Hardware delegate target (optional — defaults to CPU)
    pub delegate: Option<String>,

    /// Module-specific parameters
    pub params: HashMap<String, serde_json::Value>,

    /// Region of interest filter (only process tiles near this point)
    pub roi: Option<RegionOfInterest>,

    /// Sensor bands to use (e.g., ["B3", "B4", "B11"])
    pub bands: Option<Vec<String>>,

    /// Human-readable goal/description
    pub goal: Option<String>,
}

/// A geographic region of interest for ROI-based tile filtering.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegionOfInterest {
    pub lat: f64,
    pub lon: f64,
    pub radius_km: f64,
}

impl RegionOfInterest {
    /// Check if a lat/lon point is within this ROI.
    pub fn contains(&self, lat: f64, lon: f64) -> bool {
        // Haversine distance in km
        let r = 6371.0; // Earth radius in km
        let dlat = (lat - self.lat).to_radians();
        let dlon = (lon - self.lon).to_radians();
        let a = (dlat / 2.0).sin().powi(2)
            + self.lat.to_radians().cos()
                * lat.to_radians().cos()
                * (dlon / 2.0).sin().powi(2);
        let c = 2.0 * a.sqrt().atan2((1.0 - a).sqrt());
        let dist = r * c;
        dist <= self.radius_km
    }
}

/// Output and synchronization strategy.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OutputStrategy {
    /// Sync node identifier (e.g., "PI_JANITOR_SYNCTHING")
    pub sync_node: String,

    /// Storage target (e.g., "SANDISK_G_ARMOR_1TB")
    pub storage: String,

    /// Frontend for display (e.g., "TAURI_P51_LAPTOP")
    pub frontend: String,
}

impl MissionSpec {
    /// Load a mission spec from a JSON file.
    pub fn from_file(path: &PathBuf) -> anyhow::Result<Self> {
        let json = std::fs::read_to_string(path)?;
        let spec: MissionSpec = serde_json::from_str(&json)?;
        Ok(spec)
    }

    /// Resolve which delegate should process a tile at the given position.
    ///
    /// Iterates through modules and checks ROI filters. Returns the
    /// highest-priority delegate for this tile location.
    pub fn resolve_delegate(
        &self,
        _tile_col: usize,
        _tile_row: usize,
        lat: f64,
        lon: f64,
    ) -> Option<DelegateTarget> {
        // Find all modules whose ROI contains this lat/lon
        for module in &self.modules {
            let in_roi = module
                .roi
                .as_ref()
                .map(|r| r.contains(lat, lon))
                .unwrap_or(true); // No ROI = process all tiles

            if in_roi {
                return Some(
                    module
                        .delegate
                        .as_ref()
                        .map(|d| DelegateTarget::from_str(d))
                        .unwrap_or_default(),
                );
            }
        }
        None
    }

    /// Get all sensor bands referenced across all modules.
    pub fn all_bands(&self) -> Vec<String> {
        let mut bands: Vec<String> = self
            .modules
            .iter()
            .filter_map(|m| m.bands.as_ref())
            .flatten()
            .cloned()
            .collect();
        bands.sort();
        bands.dedup();
        bands
    }

    /// Get modules targeting a specific delegate.
    pub fn modules_for_delegate(&self, delegate: &str) -> Vec<&ModuleSpec> {
        self.modules
            .iter()
            .filter(|m| {
                m.delegate
                    .as_deref()
                    .map(|d| d == delegate)
                    .unwrap_or(false)
            })
            .collect()
    }
}
