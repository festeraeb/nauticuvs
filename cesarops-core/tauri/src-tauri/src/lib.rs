mod kml_generator;
mod erie_model;

use std::env;
use std::fs;
use std::io;
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

static BACKEND_URL: OnceLock<Mutex<Option<String>>> = OnceLock::new();
static BACKEND_CHILD: OnceLock<Mutex<Option<Child>>> = OnceLock::new();

fn url_slot() -> &'static Mutex<Option<String>> {
    BACKEND_URL.get_or_init(|| Mutex::new(None))
}

fn child_slot() -> &'static Mutex<Option<Child>> {
    BACKEND_CHILD.get_or_init(|| Mutex::new(None))
}

fn url_is_reachable(url: &str) -> bool {
    let addr = url.strip_prefix("http://").unwrap_or(url);
    TcpStream::connect(addr).is_ok()
}

fn find_repo_root() -> Option<PathBuf> {
    if let Ok(root) = env::var("BAGRECOVERY_ROOT") {
        let p = PathBuf::from(root);
        if p.join("wrecks_api").join("app.py").exists() {
            return Some(p);
        }
    }

    if let Ok(mut dir) = env::current_dir() {
        loop {
            if dir.join("wrecks_api").join("app.py").exists() {
                return Some(dir);
            }
            if !dir.pop() {
                break;
            }
        }
    }

    // Walk up from the running executable's location (works for MSI/NSIS installs
    // and when cwd is not the repo root).
    if let Ok(exe) = env::current_exe() {
        if let Some(mut dir) = exe.parent().map(|p| p.to_path_buf()) {
            loop {
                if dir.join("wrecks_api").join("app.py").exists() {
                    return Some(dir);
                }
                if !dir.pop() {
                    break;
                }
            }
        }
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for ancestor in manifest_dir.ancestors() {
        let candidate = Path::new(ancestor);
        if candidate.join("wrecks_api").join("app.py").exists() {
            return Some(candidate.to_path_buf());
        }
    }

    None
}

fn resolve_python_executable(root: &Path) -> Result<String, String> {
    if let Ok(py) = env::var("BAGRECOVERY_PYTHON") {
        return Ok(py);
    }

    let candidates = [
        root.join("venv").join("Scripts").join("python.exe"),
        root.join(".venv").join("Scripts").join("python.exe"),
    ];

    for p in candidates {
        if p.exists() {
            return Ok(p.to_string_lossy().to_string());
        }
    }

    Err(format!(
        "No Python interpreter configured. Set BAGRECOVERY_PYTHON or create {} or {}",
        root.join("venv").join("Scripts").join("python.exe").display(),
        root.join(".venv").join("Scripts").join("python.exe").display()
    ))
}

fn pick_open_port() -> io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn wait_for_port(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

#[tauri::command]
fn open_output_path(path: String) -> Result<(), String> {
    let root = find_repo_root().ok_or_else(|| {
        "Could not find Bagrecovery root (missing wrecks_api/app.py).".to_string()
    })?;

    let raw = PathBuf::from(path.trim());
    let resolved = if raw.is_absolute() { raw } else { root.join(raw) };

    if !resolved.exists() {
        return Err(format!("Path does not exist: {}", resolved.display()));
    }

    #[cfg(target_os = "windows")]
    {
        Command::new("explorer")
            .arg(&resolved)
            .spawn()
            .map_err(|e| format!("Failed to open path {}: {e}", resolved.display()))?;
        return Ok(());
    }

    #[cfg(target_os = "linux")]
    {
        Command::new("xdg-open")
            .arg(&resolved)
            .spawn()
            .map_err(|e| format!("Failed to open path {}: {e}", resolved.display()))?;
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&resolved)
            .spawn()
            .map_err(|e| format!("Failed to open path {}: {e}", resolved.display()))?;
        return Ok(());
    }

    #[allow(unreachable_code)]
    Err("Unsupported OS for open_output_path".to_string())
}

#[tauri::command]
fn ensure_backend() -> Result<String, String> {
    {
        let guard = url_slot()
            .lock()
            .map_err(|_| "Failed to lock backend URL cache".to_string())?;
        if let Some(url) = guard.as_ref() {
            if url_is_reachable(url) {
                return Ok(url.clone());
            }
        }
    }

    let root = find_repo_root().ok_or_else(|| {
        "Could not find Bagrecovery root (missing wrecks_api/app.py).".to_string()
    })?;

    // Check if another process already started the backend (e.g. another Tauri
    // instance or start_uvicorn_sniff.py) by reading the .server_port file.
    let port_file = root.join(".server_port");
    if port_file.exists() {
        if let Ok(contents) = fs::read_to_string(&port_file) {
            let addr = contents.trim();
            if !addr.is_empty() && url_is_reachable(addr) {
                let existing_url = format!("http://{}", addr);
                let mut guard = url_slot()
                    .lock()
                    .map_err(|_| "Failed to lock backend URL cache".to_string())?;
                *guard = Some(existing_url.clone());
                return Ok(existing_url);
            }
        }
    }

    let python = resolve_python_executable(&root)?;
    let port = pick_open_port().map_err(|e| format!("Failed to pick open port: {e}"))?;
    let host = "127.0.0.1";
    let api_base = format!("http://{host}:{port}");

    // Validate the chosen interpreter has required scanner/backend modules.
    let preflight = Command::new(&python)
        .current_dir(&root)
        .arg("-c")
        .arg("import fastapi,uvicorn,pyproj,rasterio,h5py")
        .output()
        .map_err(|e| format!("Failed to run python dependency preflight with '{python}': {e}"))?;
    if !preflight.status.success() {
        let stderr = String::from_utf8_lossy(&preflight.stderr);
        let stdout = String::from_utf8_lossy(&preflight.stdout);
        return Err(format!(
            "Python environment missing backend/scanner deps for '{python}'. stdout: {} stderr: {}",
            stdout.trim(),
            stderr.trim()
        ));
    }

    let log_path = root.join(".server.log");
    fs::write(
        &log_path,
        format!(
            "Starting backend with python: {}\nWorking directory: {}\nExpected API: {}\n",
            python,
            root.display(),
            api_base
        ),
    )
    .map_err(|e| format!("Failed to write backend preamble log {}: {e}", log_path.display()))?;

    let log_file = fs::OpenOptions::new()
        .append(true)
        .open(&log_path)
        .map_err(|e| format!("Failed to create backend log file {}: {e}", log_path.display()))?;
    let log_file_err = log_file
        .try_clone()
        .map_err(|e| format!("Failed to clone backend log file handle: {e}"))?;

    let child = Command::new(&python)
        .current_dir(&root)
        .arg("-m")
        .arg("uvicorn")
        .arg("wrecks_api.app:app")
        .arg("--host")
        .arg(host)
        .arg("--port")
        .arg(port.to_string())
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(log_file_err))
        .spawn()
        .map_err(|e| format!("Failed to start backend with '{python}': {e}"))?;

    {
        let mut slot = child_slot()
            .lock()
            .map_err(|_| "Failed to lock backend child state".to_string())?;
        *slot = Some(child);
    }

    if !wait_for_port(port, Duration::from_secs(15)) {
        return Err(format!(
            "Backend failed to come online at {api_base}. Check {}",
            log_path.display()
        ));
    }

    let port_file = root.join(".server_port");
    fs::write(&port_file, format!("{host}:{port}\n"))
        .map_err(|e| format!("Failed to write {}: {e}", port_file.display()))?;

    {
        let mut guard = url_slot()
            .lock()
            .map_err(|_| "Failed to lock backend URL cache".to_string())?;
        *guard = Some(api_base.clone());
    }
    Ok(api_base)
}

#[tauri::command]
fn gen_kmz(
    scan_results_path: String,
    wrecks_db_path: String,
    output_path: String,
    search_radius_m: Option<f64>,
) -> Result<String, String> {
    let radius = search_radius_m.unwrap_or(1000.0);
    kml_generator::generate_kmz(&scan_results_path, &wrecks_db_path, &output_path, radius)
}

#[tauri::command]
fn gen_kml(
    scan_results_path: String,
    wrecks_db_path: String,
    output_path: String,
    search_radius_m: Option<f64>,
) -> Result<String, String> {
    let radius = search_radius_m.unwrap_or(1000.0);
    kml_generator::generate_kml(&scan_results_path, &wrecks_db_path, &output_path, radius)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
    .plugin(tauri_plugin_dialog::init())
    .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            ensure_backend,
            open_output_path,
            gen_kmz,
            gen_kml,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
