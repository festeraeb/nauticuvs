//! CESAROPS Tauri Desktop App — Thin Backend
//!
//! Two modes:
//!   THIN    — Spawns existing Python tools (ai_director, orchestrator, etc.)
//!             Use this when cesarops-core Python env is already set up.
//!   FULL    — Includes/embeds tools. Use for standalone installer.
//!
//! The backend is a thin wrapper: spawns Python → captures stdout → sends to React frontend.

mod nasa_agent;

use std::collections::HashMap;
use std::net::{TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;
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

fn load_dotenv_map(work_dir: &PathBuf) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let path = work_dir.join(".env");
    let content = match std::fs::read_to_string(path) {
        Ok(v) => v,
        Err(_) => return map,
    };

    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Some((k, v)) = trimmed.split_once('=') {
            map.insert(k.trim().to_string(), v.trim().to_string());
        }
    }

    map
}

fn env_or_dotenv(dotenv: &HashMap<String, String>, key: &str) -> Option<String> {
    std::env::var(key).ok().or_else(|| dotenv.get(key).cloned())
}

fn provider_runtime_config(
    provider: &str,
    work_dir: &PathBuf,
    model_override: Option<&str>,
) -> Result<(String, String, bool), String> {
    let dotenv = load_dotenv_map(work_dir);
    let normalized_override = model_override
        .map(|m| m.trim())
        .filter(|m| !m.is_empty())
        .map(|m| m.to_string());

    match provider {
        "qwen" => {
            let base = env_or_dotenv(&dotenv, "QWEN_BASE_URL")
                .unwrap_or_else(|| "https://dashscope.aliyuncs.com/compatible-mode/v1".to_string());
            let model = normalized_override.unwrap_or_else(|| {
                env_or_dotenv(&dotenv, "QWEN_MODEL")
                    .unwrap_or_else(|| "qwen-plus".to_string())
            });
            let has_key = env_or_dotenv(&dotenv, "QWEN_API_KEY").is_some();
            Ok((base, model, has_key))
        }
        "koboldcpp" => {
            let base = env_or_dotenv(&dotenv, "KOBOLDCPP_BASE_URL")
                .unwrap_or_else(|| "http://127.0.0.1:5001/v1".to_string());
            let model = normalized_override.unwrap_or_else(|| {
                env_or_dotenv(&dotenv, "KOBOLDCPP_MODEL")
                    .unwrap_or_else(|| "DeepSeek-R1-Distill-Qwen-7B".to_string())
            });
            let has_key = env_or_dotenv(&dotenv, "KOBOLDCPP_API_KEY").is_some();
            Ok((base, model, has_key))
        }
        "github_sdk" => {
            let base = env_or_dotenv(&dotenv, "GITHUB_AGENT_BASE_URL")
                .unwrap_or_else(|| "http://127.0.0.1:8080/v1".to_string());
            let model = normalized_override.unwrap_or_else(|| {
                env_or_dotenv(&dotenv, "GITHUB_AGENT_MODEL")
                    .unwrap_or_else(|| "gpt-4.1".to_string())
            });
            let has_key = env_or_dotenv(&dotenv, "GITHUB_AGENT_API_KEY").is_some();
            Ok((base, model, has_key))
        }
        _ => Err(format!(
            "Unknown provider '{}'. Expected one of: qwen, koboldcpp, github_sdk",
            provider
        )),
    }
}

fn parse_host_port(base_url: &str) -> Option<(String, u16)> {
    let (default_port, stripped) = if let Some(s) = base_url.strip_prefix("https://") {
        (443u16, s)
    } else if let Some(s) = base_url.strip_prefix("http://") {
        (80u16, s)
    } else {
        (80u16, base_url)
    };

    let host_port = stripped.split('/').next()?.trim();
    if host_port.is_empty() {
        return None;
    }

    if let Some((host, port_str)) = host_port.rsplit_once(':') {
        if let Ok(port) = port_str.parse::<u16>() {
            return Some((host.to_string(), port));
        }
    }

    Some((host_port.to_string(), default_port))
}

fn tcp_reachable(base_url: &str) -> bool {
    let (host, port) = match parse_host_port(base_url) {
        Some(v) => v,
        None => return false,
    };

    let addr = format!("{}:{}", host, port);
    let addrs = match addr.to_socket_addrs() {
        Ok(v) => v,
        Err(_) => return false,
    };

    for a in addrs {
        if TcpStream::connect_timeout(&a, Duration::from_millis(1200)).is_ok() {
            return true;
        }
    }
    false
}

fn agent_provider_env(
    provider: &str,
    work_dir: &PathBuf,
    model_override: Option<&str>,
) -> Result<HashMap<String, String>, String> {
    let mut env = HashMap::new();
    let dotenv = load_dotenv_map(work_dir);
    let (base, model, has_key) = provider_runtime_config(provider, work_dir, model_override)?;

    match provider {
        "qwen" => {
            // Use existing .env/QWEN_* values with no override.
        }
        "koboldcpp" => {
            env.insert("QWEN_BASE_URL".to_string(), base);
            env.insert("QWEN_MODEL".to_string(), model);
            env.insert(
                "QWEN_API_KEY".to_string(),
                if has_key {
                    env_or_dotenv(&dotenv, "KOBOLDCPP_API_KEY").unwrap_or_else(|| "local".to_string())
                } else {
                    "local".to_string()
                },
            );
        }
        "github_sdk" => {
            env.insert("QWEN_BASE_URL".to_string(), base);
            env.insert("QWEN_MODEL".to_string(), model);
            env.insert(
                "QWEN_API_KEY".to_string(),
                if has_key {
                    env_or_dotenv(&dotenv, "GITHUB_AGENT_API_KEY").unwrap_or_else(|| "local".to_string())
                } else {
                    "local".to_string()
                },
            );
        }
        _ => {
            return Err(format!(
                "Unknown provider '{}'. Expected one of: qwen, koboldcpp, github_sdk",
                provider
            ));
        }
    }

    Ok(env)
}

/// Check provider endpoint configuration and reachability.
#[tauri::command]
async fn agent_provider_status(
    provider: Option<String>,
    work_dir: String,
    model_override: Option<String>,
) -> Result<String, String> {
    let actual_work_dir = resolve_work_dir(&work_dir);
    let selected_provider = provider.unwrap_or_else(|| "qwen".to_string());
    let (base, model, has_key) = provider_runtime_config(
        &selected_provider,
        &actual_work_dir,
        model_override.as_deref(),
    )?;
    let reachable = tcp_reachable(&base);

    let key_status = if has_key { "present" } else { "missing (or using local default)" };
    let reachability = if reachable { "reachable" } else { "not reachable" };

    Ok(format!(
        "Provider: {}\nBase URL: {}\nModel: {}\nAPI key: {}\nEndpoint: {}",
        selected_provider, base, model, key_status, reachability
    ))
}

async fn run_python_task(
    app: tauri::AppHandle,
    task_id: String,
    script: String,
    args: Vec<String>,
    cwd: Option<String>,
    extra_env: Option<HashMap<String, String>>,
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
    if let Some(env_vars) = extra_env {
        for (k, v) in env_vars {
            cmd.env(k, v);
        }
    }
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

/// Spawn a Python script and stream output to the frontend
#[tauri::command]
async fn run_task(
    app: tauri::AppHandle,
    task_id: String,
    script: String,
    args: Vec<String>,
    cwd: Option<String>,
) -> Result<TaskOutput, String> {
    run_python_task(app, task_id, script, args, cwd, None).await
}

/// Run an AI Director request
#[tauri::command]
async fn ai_direct_request(
    app: tauri::AppHandle,
    request: String,
    work_dir: String,
    provider: Option<String>,
    model_override: Option<String>,
) -> Result<TaskOutput, String> {
    let actual_work_dir = resolve_work_dir(&work_dir);
    let selected_provider = provider.unwrap_or_else(|| "qwen".to_string());
    let env = agent_provider_env(&selected_provider, &actual_work_dir, model_override.as_deref())?;

    run_python_task(
        app,
        "ai_direct".into(),
        "ai_director.py".into(),
        vec!["--request".into(), request, "--execute".into()],
        Some(actual_work_dir.to_string_lossy().to_string()),
        Some(env),
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
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![
            run_task,
            ai_direct_request,
            agent_provider_status,
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
