#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::{
    env,
    fs,
    io::{self, BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, State, WindowEvent};

#[derive(Default)]
struct RuntimeState {
    child: Mutex<Option<Child>>,
    info: Mutex<Option<LocalRuntimeInfo>>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct LocalRuntimeInfo {
    project_root: String,
    dashboard_url: String,
    mcp_url: String,
}

fn endpoint_from_line(line: &str, prefix: &str, expected_path: &str) -> Option<String> {
    let url = line.strip_prefix(prefix)?.trim();
    let rest = url.strip_prefix("http://127.0.0.1:")?;
    let (port, path) = rest.split_once('/')?;
    let port = port.parse::<u16>().ok()?;
    if port == 0 || path != expected_path {
        return None;
    }
    Some(url.to_string())
}

fn terminate(child: &mut Child) {
    if child.try_wait().ok().flatten().is_none() {
        let _ = child.kill();
    }
    let _ = child.wait();
}

fn stop_runtime(state: &RuntimeState) -> Result<(), String> {
    let mut child = state.child.lock().map_err(|_| "runtime process lock is unavailable")?;
    let mut info = state.info.lock().map_err(|_| "runtime state lock is unavailable")?;
    if let Some(mut process) = child.take() {
        terminate(&mut process);
    }
    *info = None;
    Ok(())
}

#[tauri::command]
fn start_local(project_root: String, state: State<'_, RuntimeState>) -> Result<LocalRuntimeInfo, String> {
    let root = PathBuf::from(project_root.trim());
    if project_root.trim().is_empty() {
        return Err("project folder is required".into());
    }
    let root = fs::canonicalize(&root).map_err(|_| "project folder does not exist or cannot be accessed")?;
    if !root.is_dir() {
        return Err("project path must be a directory".into());
    }

    let mut child_slot = state.child.lock().map_err(|_| "runtime process lock is unavailable")?;
    let mut info_slot = state.info.lock().map_err(|_| "runtime state lock is unavailable")?;
    if let Some(child) = child_slot.as_mut() {
        match child.try_wait().map_err(|_| "cannot inspect the AUREN CLI process")? {
            None => {
                if let Some(info) = info_slot.as_ref() {
                    if info.project_root == root.to_string_lossy() {
                        return Ok(info.clone());
                    }
                    return Err("AUREN is already running for another project. Stop it before switching projects.".into());
                }
                return Err("AUREN is already starting. Wait for startup to finish.".into());
            }
            Some(_) => {
                child_slot.take();
                *info_slot = None;
            }
        }
    }

    let executable = env::var_os("AUREN_EXECUTABLE").unwrap_or_else(|| {
        if cfg!(windows) { "auren.exe".into() } else { "auren".into() }
    });
    let mut child = Command::new(executable)
        .arg("up")
        .arg("--project-root")
        .arg(&root)
        .args(["--dashboard-port", "0", "--mcp-port", "0", "--no-connect", "--no-browser", "--no-chatgpt"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| "could not start the AUREN CLI. Install it and add auren to PATH, or set AUREN_EXECUTABLE.".to_string())?;

    let stdout = child.stdout.take().ok_or_else(|| "AUREN CLI stdout was unavailable".to_string())?;
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            let mut reader = BufReader::new(stderr);
            let mut sink = io::sink();
            let _ = io::copy(&mut reader, &mut sink);
        });
    }
    let (sender, receiver) = mpsc::channel::<String>();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => { let _ = sender.send(line); }
                Err(_) => break,
            }
        }
    });

    let deadline = Instant::now() + Duration::from_secs(35);
    let mut dashboard_url = None;
    let mut mcp_url = None;
    while Instant::now() < deadline {
        match receiver.recv_timeout(Duration::from_millis(500)) {
            Ok(line) => {
                if dashboard_url.is_none() {
                    dashboard_url = endpoint_from_line(&line, "Observation console:", "control-center");
                }
                if mcp_url.is_none() {
                    mcp_url = endpoint_from_line(&line, "MCP endpoint:", "mcp");
                }
                if dashboard_url.is_some() && mcp_url.is_some() {
                    break;
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if let Some(status) = child.try_wait().map_err(|_| "cannot inspect the AUREN CLI process")? {
                    return Err(format!("AUREN CLI exited before both services were ready (exit status: {status})"));
                }
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                terminate(&mut child);
                return Err("AUREN CLI stopped before reporting both ready endpoints".into());
            }
        }
    }

    let (Some(dashboard_url), Some(mcp_url)) = (dashboard_url, mcp_url) else {
        terminate(&mut child);
        return Err("AUREN did not report both local endpoints within 35 seconds".into());
    };

    let info = LocalRuntimeInfo {
        project_root: root.to_string_lossy().into_owned(),
        dashboard_url,
        mcp_url,
    };
    *info_slot = Some(info.clone());
    *child_slot = Some(child);
    Ok(info)
}

#[tauri::command]
fn stop_local(state: State<'_, RuntimeState>) -> Result<(), String> {
    stop_runtime(state.inner())
}

fn main() {
    tauri::Builder::default()
        .manage(RuntimeState::default())
        .invoke_handler(tauri::generate_handler![start_local, stop_local])
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                let state = window.state::<RuntimeState>();
                let _ = stop_runtime(state.inner());
            }
        })
        .run(tauri::generate_context!())
        .expect("failed to start AUREN Desktop");
}

#[cfg(test)]
mod tests {
    use super::endpoint_from_line;

    #[test]
    fn accepts_only_the_expected_loopback_endpoint_line() {
        assert_eq!(
            endpoint_from_line(
                "Observation console: http://127.0.0.1:43120/control-center",
                "Observation console:",
                "control-center",
            ),
            Some("http://127.0.0.1:43120/control-center".to_string()),
        );
        assert_eq!(
            endpoint_from_line(
                "MCP endpoint: http://127.0.0.1:43121/mcp",
                "MCP endpoint:",
                "mcp",
            ),
            Some("http://127.0.0.1:43121/mcp".to_string()),
        );
    }

    #[test]
    fn rejects_non_loopback_and_wrong_path_endpoint_lines() {
        assert!(endpoint_from_line(
            "Observation console: http://0.0.0.0:43120/control-center",
            "Observation console:",
            "control-center",
        ).is_none());
        assert!(endpoint_from_line(
            "Observation console: http://127.0.0.1:43120/",
            "Observation console:",
            "control-center",
        ).is_none());
        assert!(endpoint_from_line(
            "MCP endpoint: http://127.0.0.1:0/mcp",
            "MCP endpoint:",
            "mcp",
        ).is_none());
    }
}
