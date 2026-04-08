use serde::{Deserialize, Serialize};
use std::process::Command;
use std::path::PathBuf;

#[derive(Serialize, Deserialize)]
pub struct SwarmRequest {
    pub lake: String,
    pub year: u32,
    pub sensors: String,
}

/// Command to search NASA Catalog via Rust SDK
#[tauri::command]
pub async fn search_nasa_granules(
    bbox: Vec<f64>,
    start_date: String,
    end_date: String,
    sensor: String,
) -> Result<serde_json::Value, String> {
    // TODO: Integrate nasa-rs SDK here for live CMR/HyP3 queries.
    // For now, we return a mock manifest to unblock the UI.
    let mock_granules = vec![
        serde_json::json!({"id": "HLS.S30.T16TDN.20240601", "sensor": "HLS", "cloud_cover": 5}),
        serde_json::json!({"id": "S1A_IW_SLC__1SDV.20240601", "sensor": "SAR", "polarization": "VV+VH"}),
    ];
    Ok(serde_json::json!({ "granules": mock_granules, "count": mock_granules.len() }))
}

/// Command to trigger the Python Swarm with a specific task
#[tauri::command]
pub async fn trigger_swarm_download(
    work_dir: String,
    request: SwarmRequest,
) -> Result<String, String> {
    let python = if cfg!(windows) { "python" } else { "python3" };
    let script = "batch_download_manager.py";
    
    let output = Command::new(python)
        .current_dir(PathBuf::from(work_dir))
        .arg(script)
        .arg("--lakes")
        .arg(&request.lake)
        .arg("--start")
        .arg(request.year.to_string())
        .arg("--end")
        .arg(request.year.to_string())
        .arg("--sensors")
        .arg(&request.sensors)
        .output()
        .map_err(|e| format!("Failed to start download: {}", e))?;

    if output.status.success() {
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    } else {
        Err(format!("Download failed: {}", String::from_utf8_lossy(&output.stderr)))
    }
}