//! Hardware delegate routing.
//!
//! Each tile can be assigned to a specific processing delegate:
//! - CPU (default, for light work)
//! - Coral TPU (INT8 quantized models)
//! - NVIDIA GPU (CUDA/F16)
//! - Hybrid (multi-pass across delegates)
//!
//! The delegate tag in the mission spec JSON controls routing.

use serde::{Deserialize, Serialize};

/// Target hardware delegate for tile processing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DelegateTarget {
    /// Default CPU processing (no hardware acceleration)
    #[serde(rename = "CPU")]
    Cpu,

    /// Google Coral TPU (INT8 Edge TPU models)
    #[serde(rename = "CORAL_TPU_INT8")]
    CoralTpuInt8,

    /// NVIDIA GPU (CUDA, FP16/FP32)
    #[serde(rename = "NVIDIA_GPU")]
    NvidiaGpu,

    /// Hybrid: split work across CPU + GPU
    #[serde(rename = "HYBRID")]
    Hybrid,

    /// Skip this tile (ROI filter excluded it)
    #[serde(rename = "SKIP")]
    Skip,
}

impl DelegateTarget {
    /// Parse from a string (from JSON delegate field).
    pub fn from_str(s: &str) -> Self {
        match s.to_uppercase().as_str() {
            "CORAL_TPU_INT8" | "CORAL" | "TPU" => Self::CoralTpuInt8,
            "NVIDIA_GPU" | "GPU" | "CUDA" | "P1000" => Self::NvidiaGpu,
            "HYBRID" => Self::Hybrid,
            "SKIP" => Self::Skip,
            _ => Self::Cpu,
        }
    }

    /// Check if this delegate requires GPU hardware.
    pub fn needs_gpu(&self) -> bool {
        matches!(self, Self::NvidiaGpu | Self::Hybrid)
    }

    /// Check if this delegate requires a Coral TPU.
    pub fn needs_tpu(&self) -> bool {
        matches!(self, Self::CoralTpuInt8)
    }

    /// Check if this tile should be skipped.
    pub fn is_skip(&self) -> bool {
        matches!(self, Self::Skip)
    }
}

impl Default for DelegateTarget {
    fn default() -> Self {
        Self::Cpu
    }
}

/// Delegate routing table: groups tiles by their assigned delegate.
#[derive(Debug, Default)]
pub struct DelegateRouter {
    pub cpu_tiles: Vec<String>,
    pub tpu_tiles: Vec<String>,
    pub gpu_tiles: Vec<String>,
    pub skipped: Vec<String>,
}

impl DelegateRouter {
    /// Route a tile ID to its delegate queue.
    pub fn route(&mut self, tile_id: &str, delegate: DelegateTarget) {
        match delegate {
            DelegateTarget::Cpu => self.cpu_tiles.push(tile_id.to_string()),
            DelegateTarget::CoralTpuInt8 => self.tpu_tiles.push(tile_id.to_string()),
            DelegateTarget::NvidiaGpu => self.gpu_tiles.push(tile_id.to_string()),
            DelegateTarget::Hybrid => {
                // Hybrid: route to both CPU and GPU queues
                self.cpu_tiles.push(tile_id.to_string());
                self.gpu_tiles.push(tile_id.to_string());
            }
            DelegateTarget::Skip => self.skipped.push(tile_id.to_string()),
        }
    }

    /// Total tiles routed.
    pub fn total(&self) -> usize {
        self.cpu_tiles.len()
            + self.tpu_tiles.len()
            + self.gpu_tiles.len()
            + self.skipped.len()
    }

    /// Print routing summary.
    pub fn summary(&self) -> String {
        format!(
            "CPU: {} | TPU: {} | GPU: {} | Skipped: {} | Total: {}",
            self.cpu_tiles.len(),
            self.tpu_tiles.len(),
            self.gpu_tiles.len(),
            self.skipped.len(),
            self.total()
        )
    }
}
