//! GDAL OpenCL Warp for GPU-accelerated resampling.
//!
//! Uses the P1000 GPU via OpenCL to warp 30m Landsat pixels
//! to the 10m Sentinel-2 grid. This is the "Monster Hunter" baseline —
//! high-quality Lanczos resampling that prevents aliasing artifacts
//! in the Curvelet spine-locking and thermal cold-sink detection.
//!
//! The warp writes a Unified Master TIFF (all bands aligned) back
//! to the G-Armor for the Pi to slice.
//!
//! Tunable parameters:
//!   - working_memory_mb: VRAM budget (default 4000 for P1000)
//!   - block_size: internal tile size for faster mmap access
//!   - If "GPU out of memory", Qwen can lower working_memory_mb to 2000

use std::path::Path;
use std::process::Command;
use std::time::Instant;

use serde::{Deserialize, Serialize};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum WarpError {
    #[error("gdalwarp not found")]
    GdalNotFound,

    #[error("warp failed: exit {code}: {stderr}")]
    WarpFailed { code: i32, stderr: String },

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

/// Result of a GPU-accelerated warp operation.
#[derive(Debug, Serialize, Deserialize)]
pub struct WarpResult {
    pub input: String,
    pub output: String,
    pub duration_s: f64,
    pub resampling: String,
    pub gpu_accelerated: bool,
    pub working_memory_mb: u32,
    pub block_size: u32,
}

/// GDAL warp configuration — tunable by Qwen.
#[derive(Clone)]
pub struct WarpConfig {
    /// Working memory in MB (VRAM budget). Default: 4000 for P1000.
    /// If OOM, Qwen can lower this to 2000.
    pub working_memory_mb: u32,

    /// Internal block size for tiled output. Default: 512.
    /// Smaller blocks = faster mmap reads, larger = fewer I/O ops.
    pub block_size: u32,
}

impl Default for WarpConfig {
    fn default() -> Self {
        Self {
            working_memory_mb: 4000,
            block_size: 512,
        }
    }
}

impl WarpConfig {
    /// Conservative config for when GPU is also running probe logic.
    /// Leaves 2GB VRAM free for other tasks.
    pub fn conservative() -> Self {
        Self {
            working_memory_mb: 2000,
            block_size: 512,
        }
    }
}

/// Execute GPU-accelerated warp via gdalwarp CLI.
///
/// Offloads 30m→10m resampling to P1000 via OpenCL,
/// preventing system RAM from choking on large GeoTIFFs.
pub fn gpu_warp<P: AsRef<Path>>(
    input: P,
    output: P,
    bounds: Option<(f64, f64, f64, f64)>,
) -> Result<WarpResult, WarpError> {
    gpu_warp_with_config(input, output, bounds, WarpConfig::default())
}

/// Execute GPU-accelerated warp with custom configuration.
pub fn gpu_warp_with_config<P: AsRef<Path>>(
    input: P,
    output: P,
    bounds: Option<(f64, f64, f64, f64)>,
    config: WarpConfig,
) -> Result<WarpResult, WarpError> {
    let input = input.as_ref();
    let output = output.as_ref();

    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let start = Instant::now();

    let mut cmd = Command::new("gdalwarp");

    // Target CRS
    cmd.arg("-t_srs").arg("EPSG:4326");

    // Target resolution: ~10m in decimal degrees
    cmd.arg("-tr").arg("0.00008983").arg("0.00008983");

    // High-quality resampling (GPU-accelerated)
    cmd.arg("-r").arg("lanczos");

    // GPU acceleration via OpenCL
    cmd.arg("-wo").arg("USE_OPENCL=TRUE");
    cmd.arg("-wo").arg("NUM_THREADS=ALL_CPUS");

    // Working memory: tunable VRAM budget
    cmd.arg("-wm").arg(format!("{}", config.working_memory_mb));

    // Parallel I/O
    cmd.arg("-multi");

    // Output: internally tiled for faster mmap access
    cmd.arg("-co").arg("TILED=YES");
    cmd.arg("-co").arg(format!("BLOCKXSIZE={}", config.block_size));
    cmd.arg("-co").arg(format!("BLOCKYSIZE={}", config.block_size));
    cmd.arg("-co").arg("COMPRESS=DEFLATE");

    // Optional bounding box
    if let Some((xmin, ymin, xmax, ymax)) = bounds {
        cmd.arg("-te")
            .arg(format!("{}", xmin))
            .arg(format!("{}", ymin))
            .arg(format!("{}", xmax))
            .arg(format!("{}", ymax));
    }

    // Overwrite existing
    cmd.arg("-overwrite");

    // Input and output
    cmd.arg(input).arg(output);

    let child = cmd.output()?;
    let duration = start.elapsed().as_secs_f64();

    if !child.status.success() {
        let code = child.status.code().unwrap_or(-1);
        let stderr = String::from_utf8_lossy(&child.stderr).to_string();
        return Err(WarpError::WarpFailed { code, stderr });
    }

    Ok(WarpResult {
        input: input.display().to_string(),
        output: output.display().to_string(),
        duration_s: duration,
        resampling: "lanczos".to_string(),
        gpu_accelerated: true,
        working_memory_mb: config.working_memory_mb,
        block_size: config.block_size,
    })
}

/// Get the GeoTransform from any GDAL-readable file by shelling out to `gdalinfo -json`.
///
/// Returns `None` if `gdalinfo` is unavailable or the file has no georeference.
/// The returned array is the standard 6-element GDAL GeoTransform:
///   [origin_x, pixel_width, row_rot, origin_y, col_rot, pixel_height]
pub fn gdalinfo_geo_transform<P: AsRef<Path>>(path: P) -> Option<crate::io::geotiff::GeoTransform> {
    let output = Command::new("gdalinfo")
        .arg("-json")
        .arg(path.as_ref())
        .output()
        .ok()?;

    if !output.status.success() {
        return None;
    }

    let json: serde_json::Value = serde_json::from_slice(&output.stdout).ok()?;
    let arr = json.get("geoTransform")?.as_array()?;
    if arr.len() < 6 {
        return None;
    }

    let mut gt = [0.0f64; 6];
    for (i, v) in arr.iter().enumerate().take(6) {
        gt[i] = v.as_f64()?;
    }
    Some(gt)
}

/// Returns true if a file's pixel size indicates a projected CRS (meters, not degrees).
///
/// Pixel widths > 1.0 unit are meters (UTM, etc.). Geographic lat/lon rasters
/// have sub-degree pixel sizes (< 0.01 typically).
/// Falls back to `false` if `gdalinfo` is unavailable.
pub fn is_projected_crs<P: AsRef<Path>>(path: P) -> bool {
    match gdalinfo_geo_transform(path) {
        Some(gt) => gt[1].abs() > 1.0 || gt[5].abs() > 1.0,
        None => false,
    }
}

/// CRS hint derived from a GeoTransform without calling any external tool.
///
/// Used by the anchor calculator to set `native_crs` when no GDAL is available.
/// Not definitive — just a heuristic for the sidecar JSON.
pub fn crs_hint_from_geo_transform(geo_transform: &crate::io::geotiff::GeoTransform) -> String {
    if geo_transform[1].abs() > 1.0 || geo_transform[5].abs() > 1.0 {
        // Pixel size is in meters — almost certainly a projected CRS (UTM)
        String::from("PROJECTED:UNKNOWN")
    } else {
        String::from("EPSG:4326")
    }
}

/// Reproject a GeoTIFF to EPSG:4326 using gdalwarp if it is in a projected CRS.
///
/// Returns the path to use for slicing: the warped output if reprojection was
/// needed, or the original path if it was already geographic.
pub fn normalize_to_wgs84<P: AsRef<Path>, Q: AsRef<Path>>(
    input: P,
    output_dir: Q,
    config: WarpConfig,
) -> Result<std::path::PathBuf, WarpError> {
    let input = input.as_ref();
    let output_dir = output_dir.as_ref();

    if !is_projected_crs(input) {
        return Ok(input.to_path_buf());
    }

    let stem = input
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy();
    let warped = output_dir.join(format!("{}_wgs84.tif", stem));

    gpu_warp_with_config(input, &warped, None, config)?;
    Ok(warped)
}

/// Check if gdalwarp is available.
pub fn check_gpu_warp_available() -> bool {
    Command::new("gdalwarp")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_gdal_version_check() {
        let available = check_gpu_warp_available();
        println!("GDAL available: {}", available);
    }

    #[test]
    fn test_config_defaults() {
        let cfg = WarpConfig::default();
        assert_eq!(cfg.working_memory_mb, 4000);
        assert_eq!(cfg.block_size, 512);

        let cons = WarpConfig::conservative();
        assert_eq!(cons.working_memory_mb, 2000);
    }
}
