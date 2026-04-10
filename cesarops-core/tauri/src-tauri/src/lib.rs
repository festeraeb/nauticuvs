//! CESAROPS Tauri Desktop App — Thin Backend
//!
//! Two modes:
//!   THIN    — Spawns existing Python tools (ai_director, orchestrator, etc.)
//!             Use this when cesarops-core Python env is already set up.
//!   FULL    — Includes/embeds tools. Use for standalone installer.
//!
//! The backend is a thin wrapper: spawns Python → captures stdout → sends to React frontend.

mod nasa_agent;

use std::path::PathBuf;
use std::process::Command;
use tauri::Emitter;
use serde::{Deserialize, Serialize};

/// Resolve the cesarops-core directory relative to this binary's location.
/// Falls back to the current working directory if not found.
fn resolve_core_dir() -> PathBuf {
    // Strategy 1: Walk up from cwd. Covers:
    //   dev:     tauri/ -> .. = cesarops-core/
    //   release: tauri/src-tauri/target/release/ -> ../../../../ = cesarops-core/
    if let Ok(cwd) = std::env::current_dir() {
        let up_paths = ["..", "../..", "../../..", "../../../..", "../../../../.."];
        for rel in &up_paths {
            if let Some(dir) = cwd.join(rel).canonicalize().ok() {
                if dir.join("ai_director.py").exists() {
                    return dir;
                }
            }
        }
    }

    // Strategy 2: Walk up from the running executable (up to 6 levels).
    // Release binary sits at tauri/src-tauri/target/release/ — 4 levels deep.
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.clone();
        for _ in 0..6 {
            dir = match dir.parent() {
                Some(p) => p.to_path_buf(),
                None => break,
            };
            if let Ok(canonical) = dir.canonicalize() {
                if canonical.join("ai_director.py").exists() {
                    return canonical;
                }
            }
        }
    }

    // Strategy 3: Check environment variable (set by user or installer)
    if let Ok(dir) = std::env::var("CESAROPS_CORE_DIR") {
        let p = PathBuf::from(&dir);
        if p.join("ai_director.py").exists() {
            return p;
        }
    }

    // Strategy 4: Check common user development paths (both USERPROFILE and HOME)
    let home_vars = ["USERPROFILE", "HOME"];
    for var in &home_vars {
        if let Ok(home) = std::env::var(var) {
            let dev_paths = [
                "programming\\cesarops-core",
                "programming/cesarops-core",
                "Projects\\cesarops-core",
                "Projects/cesarops-core",
                "Desktop\\cesarops-core",
                "Documents\\cesarops-core",
            ];
            for sub in &dev_paths {
                let candidate = PathBuf::from(&home).join(sub);
                if candidate.join("ai_director.py").exists() {
                    return candidate;
                }
                if let Some(dir) = candidate.canonicalize().ok() {
                    if dir.join("ai_director.py").exists() {
                        return dir;
                    }
                }
            }
        }
    }

    // Strategy 5: Fall back to current working directory
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// Resolve a user-provided work directory. If it's empty, relative (like ".."),
/// or doesn't contain ai_director.py, use resolve_core_dir() instead.
pub(crate) fn resolve_work_dir(user_dir: &str) -> PathBuf {
    // If empty, use resolve_core_dir()
    if user_dir.is_empty() {
        return resolve_core_dir();
    }

    let p = PathBuf::from(user_dir);

    // If relative (e.g. "..", "../"), resolve it
    if !p.is_absolute() {
        if let Ok(cwd) = std::env::current_dir() {
            let resolved = cwd.join(&p).canonicalize().ok();
            if let Some(dir) = &resolved {
                // Verify it looks like cesarops-core
                if dir.join("ai_director.py").exists() {
                    return dir.clone();
                }
            }
        }
        // Relative path didn't resolve to cesarops-core → use auto-detect
        return resolve_core_dir();
    }

    // Absolute path — verify it contains our scripts
    if p.join("ai_director.py").exists() {
        return p;
    }

    // Doesn't look like cesarops-core → use auto-detect
    resolve_core_dir()
}

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

    // Resolve work directory using the core-dir detection logic
    let work_dir = {
        let raw = match &cwd {
            Some(d) if !d.is_empty() => d.as_str(),
            _ => "",
        };
        resolve_work_dir(raw).to_string_lossy().to_string()
    };

    let mut cmd = Command::new(python);
    cmd.current_dir(&work_dir);
    cmd.env("PYTHONIOENCODING", "utf-8");  // Force UTF-8 output
    cmd.arg(&script);
    cmd.args(&args);
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());

    let mut child = cmd.spawn()
        .map_err(|e| format!("Failed to spawn '{}': {} (cwd={})", script, e, work_dir))?;

    app.emit("task_started", &TaskOutput {
        id: task_id.clone(),
        status: "running".into(),
        stdout: format!("Spawned: {} {}\nWorking dir: {}\n---\n", python, script, work_dir),
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
    let actual_work_dir = resolve_work_dir(&work_dir);

    run_task(
        app,
        "ai_direct".into(),
        "ai_director.py".into(),
        vec!["--request".into(), request, "--execute".into()],
        Some(actual_work_dir.to_string_lossy().to_string()),
    ).await
}

/// Run a background probe
#[tauri::command]
async fn run_background_probe(
    app: tauri::AppHandle,
    work_dir: String,
) -> Result<TaskOutput, String> {
    let actual_work_dir = resolve_work_dir(&work_dir);

    run_task(
        app,
        "probe".into(),
        "background_probe.py".into(),
        vec!["--once".into()],
        Some(actual_work_dir.to_string_lossy().to_string()),
    ).await
}

/// Check node status (Pi, Xenon connectivity)
#[tauri::command]
async fn check_nodes(
    app: tauri::AppHandle,
    work_dir: String,
) -> Result<TaskOutput, String> {
    let actual_work_dir = resolve_work_dir(&work_dir);

    run_task(
        app,
        "status".into(),
        "cesarops_orchestrator.py".into(),
        vec!["--status".into()],
        Some(actual_work_dir.to_string_lossy().to_string()),
    ).await
}

/// Return the resolved absolute path to cesarops-core/ so the frontend
/// can store it and pass it back in subsequent commands.
#[tauri::command]
fn get_work_dir() -> String {
    resolve_core_dir().to_string_lossy().to_string()
}

/// List known wrecks
#[tauri::command]
async fn list_wrecks(work_dir: String) -> Result<String, String> {
    let actual_work_dir = resolve_work_dir(&work_dir);

    let output = Command::new("python")
        .arg("wreck_scraper.py")
        .arg("--list")
        .current_dir(&actual_work_dir)
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
            get_work_dir,
            nasa_agent::search_nasa_granules,
            nasa_agent::trigger_swarm_download,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
