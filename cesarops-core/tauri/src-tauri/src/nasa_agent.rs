use serde::{Deserialize, Serialize};
use std::process::Command;
use std::path::PathBuf;

#[derive(Serialize, Deserialize)]
pub struct SwarmRequest {
    pub lake: String,
    pub year: u32,
    pub sensors: String,
}

/// Query NASA CMR for real granules via cmr_search.py.
/// bbox = [lat_min, lon_min, lat_max, lon_max]
#[tauri::command]
pub async fn search_nasa_granules(
    work_dir: String,
    bbox: Vec<f64>,
    start_date: String,
    end_date: String,
    sensor: String,
) -> Result<serde_json::Value, String> {
    let python = if cfg!(windows) { "python" } else { "python3" };
    let cwd = if work_dir.is_empty() {
        std::env::current_dir()
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| ".".into())
    } else {
        work_dir.clone()
    };

    let bbox_str = bbox
        .iter()
        .map(|v| v.to_string())
        .collect::<Vec<_>>()
        .join(",");

    let output = Command::new(python)
        .current_dir(PathBuf::from(&cwd))
        .arg("cmr_search.py")
        .arg("--bbox").arg(&bbox_str)
        .arg("--start").arg(&start_date)
        .arg("--end").arg(&end_date)
        .arg("--sensor").arg(&sensor)
        .arg("--max-results").arg("50")
        .output()
        .map_err(|e| format!("Failed to launch cmr_search.py: {}", e))?;

    if output.status.success() {
        let raw = String::from_utf8_lossy(&output.stdout);
        serde_json::from_str::<serde_json::Value>(&raw)
            .map_err(|e| format!("Failed to parse CMR JSON: {} — raw: {}", e, &raw[..raw.len().min(200)]))
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(format!("cmr_search.py error: {}", &stderr[..stderr.len().min(400)]))
    }
}

/// Trigger a Python swarm download for a single lake/year (streaming via run_task).
/// For multi-lake multi-year downloads, use run_task directly with batch_download_manager.py.
#[tauri::command]
pub async fn trigger_swarm_download(
    work_dir: String,
    request: SwarmRequest,
) -> Result<String, String> {
    let python = if cfg!(windows) { "python" } else { "python3" };
    let cwd = if work_dir.is_empty() { ".".to_string() } else { work_dir };

    let output = Command::new(python)
        .current_dir(PathBuf::from(cwd))
        .arg("batch_download_manager.py")
        .arg("--lakes").arg(&request.lake)
        .arg("--start").arg(request.year.to_string())
        .arg("--end").arg(request.year.to_string())
        .arg("--sensors").arg(&request.sensors)
        .output()
        .map_err(|e| format!("Failed to start download: {}", e))?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(format!("Download failed: {}", String::from_utf8_lossy(&output.stderr)))
    }
}