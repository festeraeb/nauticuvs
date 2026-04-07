//! Folder watcher for the preprocessing pipeline.
//!
//! Watches the `PREPROCESS_IN` directory on the G-Armor drive.
//! When a new raw GeoTIFF or `.job` file is dropped in, triggers
//! the GDAL OpenCL warp on the P1000 GPU and writes the aligned
//! Master Stack to `READY_TO_SLICE`.

use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::Duration;

use notify::{Config, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use tracing::{error, info, warn};

use crate::io::gdal_warp::{self, WarpConfig};

/// Watches a directory for new GeoTIFF/`.job` files and triggers GPU warp.
pub struct PreprocessWatcher {
    watch_path: PathBuf,
    output_dir: PathBuf,
    warp_config: WarpConfig,
}

impl PreprocessWatcher {
    pub fn new(watch_path: PathBuf, output_dir: PathBuf) -> Self {
        Self {
            watch_path,
            output_dir,
            warp_config: WarpConfig::default(),
        }
    }

    /// Create with custom warp config.
    pub fn with_config(
        watch_path: PathBuf,
        output_dir: PathBuf,
        warp_config: WarpConfig,
    ) -> Self {
        Self {
            watch_path,
            output_dir,
            warp_config,
        }
    }

    /// Start watching. Blocks the calling thread.
    pub fn run(&self) -> Result<(), Box<dyn std::error::Error>> {
        info!("Watching {:?} for new mission jobs...", self.watch_path);

        let (tx, rx) = mpsc::channel();
        let mut watcher = RecommendedWatcher::new(
            tx,
            Config::default()
                .with_poll_interval(Duration::from_secs(2)),
        )?;

        watcher.watch(&self.watch_path, RecursiveMode::Recursive)?;

        for result in rx {
            match result {
                Ok(event) => {
                    if let EventKind::Create(_) = event.kind {
                        for path in event.paths {
                            self.on_new_file(&path);
                        }
                    }
                }
                Err(e) => warn!("Watch error: {}", e),
            }
        }

        Ok(())
    }

    fn on_new_file(&self, path: &Path) {
        let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");

        let should_process = matches!(ext, "tif" | "tiff" | "job");
        if !should_process {
            return;
        }

        // Skip temp/lock files
        let name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("");
        if name.starts_with('.') || name.starts_with('~') {
            return;
        }

        info!("New mission job detected: {:?}", path);

        // Generate output path
        let output_name = format!("aligned_{}", path.file_stem().unwrap().to_string_lossy());
        let output_path = self.output_dir.join(format!("{}.tif", output_name));

        // Run GPU warp with configured VRAM budget
        match gdal_warp::gpu_warp_with_config(path, &output_path, None, self.warp_config.clone()) {
            Ok(warp_info) => {
                info!(
                    "GPU Warp complete: {:?} → {:?} ({:.1}s, wm={}MB, block={})",
                    path, output_path, warp_info.duration_s,
                    warp_info.working_memory_mb, warp_info.block_size
                );
            }
            Err(e) => {
                error!("GPU Warp failed for {:?}: {}", path, e);
                // If OOM, retry with conservative config
                if let gdal_warp::WarpError::WarpFailed { ref stderr, .. } = e {
                    if stderr.to_lowercase().contains("opencl") || stderr.to_lowercase().contains("memory") {
                        warn!("OpenCL OOM detected — retrying with conservative config (wm=2000)");
                        let cons_config = WarpConfig::conservative();
                        match gdal_warp::gpu_warp_with_config(path, &output_path, None, cons_config) {
                            Ok(retry_info) => {
                                info!(
                                    "GPU Warp (conservative) succeeded: {:?} → {:?} ({:.1}s)",
                                    path, output_path, retry_info.duration_s
                                );
                                return;
                            }
                            Err(retry_err) => {
                                error!("Conservative retry also failed: {}", retry_err);
                            }
                        }
                    }
                }
                // Move to error folder for later retry
                let err_dir = self.output_dir.parent().unwrap().join("WARP_ERRORS");
                std::fs::create_dir_all(&err_dir).ok();
                if let Some(dest) = err_dir.join(path.file_name().unwrap()).to_str() {
                    std::fs::copy(path, dest).ok();
                }
            }
        }
    }
}
