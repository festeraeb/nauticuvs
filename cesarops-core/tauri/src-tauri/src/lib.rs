//! CESAROPS Tauri Desktop App — Thin Backend
//!
//! Two modes:
//!   THIN    — Spawns existing Python tools (ai_director, orchestrator, etc.)
//!             Use this when cesarops-core Python env is already set up.
//!   FULL    — Includes/embeds tools. Use for standalone installer.
//!
//! The backend is a thin wrapper: spawns Python → captures stdout → sends to React frontend.

use std::process::Command;
use tauri::Emitter;
use serde::{Deserialize, Serialize};

/// App mode — set at build or runtime
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum AppMode {
    /// Spawn external Python scripts (default for dev/existing setup)
    Thin,
    /// Self-contained — tools bundled (for complete installer)
    Full,
}

impl AppMode {
    pub fn python(&self) -> &'static str {
        match self {
            Self::Thin => "python",
            Self::Full => "python",
        }
    }
}

/// Task result sent to the frontend
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskOutput {
    pub id: String,
    pub status: String,   // "running", "success", "error", "cancelled"
    pub stdout: String,
    pub stderr: String,
    pub duration_s: f64,
}

/// Spawn a Python script and stream output to the frontend
#[tauri::command]
async fn run_task(
    app: tauri::AppHandle,
    task_id: String,
    script: String,
    args: Vec<String>,
    cwd: Option<String>,
) -> Result<TaskOutput, String> {
    let start = std::time::Instant::now();

    let mode = AppMode::Thin;
    let python = mode.python();

    let mut cmd = Command::new(python);
    cmd.arg(&script);
    cmd.args(&args);
    if let Some(dir) = &cwd {
        cmd.current_dir(dir);
    }
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());

    let mut child = cmd.spawn()
        .map_err(|e| format!("Failed to start {}: {}", script, e))?;

    app.emit("task_started", &TaskOutput {
        id: task_id.clone(),
        status: "running".into(),
        stdout: String::new(),
        stderr: String::new(),
        duration_s: 0.0,
    }).ok();

    // Read stdout line by line
    let mut stdout_buf = String::new();
    let mut stderr_buf = String::new();

    if let Some(ref mut stdout) = child.stdout {
        use std::io::BufRead;
        let reader = std::io::BufReader::new(stdout);
        for line in reader.lines() {
            if let Ok(line) = line {
                stdout_buf.push_str(&line);
                stdout_buf.push('\n');
                app.emit("task_output", &TaskOutput {
                    id: task_id.clone(),
                    status: "running".into(),
                    stdout: line.clone(),
                    stderr: String::new(),
                    duration_s: start.elapsed().as_secs_f64(),
                }).ok();
            }
        }
    }

    if let Some(ref mut stderr) = child.stderr {
        use std::io::BufRead;
        let reader = std::io::BufReader::new(stderr);
        for line in reader.lines() {
            if let Ok(line) = line {
                stderr_buf.push_str(&line);
                stderr_buf.push('\n');
            }
        }
    }

    let exit_status = child.wait()
        .map_err(|e| format!("Failed to wait: {}", e))?;

    let duration = start.elapsed().as_secs_f64();
    let status = if exit_status.success() { "success" } else { "error" };

    let output = TaskOutput {
        id: task_id.clone(),
        status: status.into(),
        stdout: stdout_buf.clone(),
        stderr: stderr_buf.clone(),
        duration_s: duration,
    };

    app.emit("task_complete", &output).ok();
    Ok(output)
}

/// Run an AI Director request
#[tauri::command]
async fn ai_direct_request(
    app: tauri::AppHandle,
    request: String,
    work_dir: String,
) -> Result<TaskOutput, String> {
    run_task(
        app,
        "ai_direct".into(),
        "ai_director.py".into(),
        vec!["--request".into(), request, "--execute".into()],
        Some(work_dir),
    ).await
}

/// Run a background probe
#[tauri::command]
async fn run_background_probe(
    app: tauri::AppHandle,
    work_dir: String,
) -> Result<TaskOutput, String> {
    run_task(
        app,
        "probe".into(),
        "background_probe.py".into(),
        vec!["--once".into()],
        Some(work_dir),
    ).await
}

/// Check node status (Pi, Xenon connectivity)
#[tauri::command]
async fn check_nodes(
    app: tauri::AppHandle,
    work_dir: String,
) -> Result<TaskOutput, String> {
    run_task(
        app,
        "status".into(),
        "cesarops_orchestrator.py".into(),
        vec!["--status".into()],
        Some(work_dir),
    ).await
}

/// List known wrecks
#[tauri::command]
async fn list_wrecks(work_dir: String) -> Result<String, String> {
    let output = Command::new("python")
        .arg("wreck_scraper.py")
        .arg("--list")
        .current_dir(&work_dir)
        .output()
        .map_err(|e| format!("Failed: {}", e))?;

    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            run_task,
            ai_direct_request,
            run_background_probe,
            check_nodes,
            list_wrecks,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
