//! Folder watcher for the preprocessing pipeline.
//!
//! Watches the `PREPROCESS_IN` directory on the G-Armor drive.
//! When a new raw GeoTIFF or `.job` file is dropped in, triggers
//! the GDAL OpenCL warp on the P1000 GPU and writes the aligned
//! Master Stack to `READY_TO_SLICE`.

use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::Duration;

use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use tracing::{error, info, warn};

use crate::io::gdal_warp;

/// Watches a directory for new GeoTIFF/`.job` files and triggers GPU warp.
pub struct PreprocessWatcher {
    watch_path: PathBuf,
    output_dir: PathBuf,
}

impl PreprocessWatcher {
    pub fn new(watch_path: PathBuf, output_dir: PathBuf) -> Self {
        Self {
            watch_path,
            output_dir,
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

        // Run GPU warp
        match gdal_warp::gpu_warp(path, &output_path, None) {
            Ok(warp_info) => {
                info!(
                    "GPU Warp complete: {:?} → {:?} ({:.1}s, {})",
                    path, output_path, warp_info.duration_s, warp_info.resampling
                );
            }
            Err(e) => {
                error!("GPU Warp failed for {:?}: {}", path, e);
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
