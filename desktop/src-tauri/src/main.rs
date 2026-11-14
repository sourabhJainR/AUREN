#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Serialize;
use std::{
    env,
    ffi::OsString,
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

fn cli_command_from_config(
    executable: Option<OsString>,
    cli_path: Option<OsString>,
    python: Option<OsString>,
) -> (OsString, Vec<OsString>) {
    if let Some(executable) = executable {
        return (executable, vec!["up".into()]);
    }
    if let Some(cli_path) = cli_path {
        let python = python.unwrap_or_else(|| {
            if cfg!(windows) { "python.exe".into() } else { "python3".into() }
        });
        return (python, vec![cli_path, "up".into()]);
    }
    let executable = if cfg!(windows) { "auren.exe".into() } else { "auren".into() };
    (executable, vec!["up".into()])
}

fn cli_command() -> (OsString, Vec<OsString>) {
    cli_command_from_config(
        env::var_os("AUREN_EXECUTABLE"),
        env::var_os("AUREN_CLI_PATH"),
        env::var_os("AUREN_PYTHON_EXECUTABLE"),
    )
}

fn verification_command_from_config(
    executable: Option<OsString>,
    cli_path: Option<OsString>,
    python: Option<OsString>,
    dashboard_url: &str,
    mcp_url: &str,
) -> (OsString, Vec<OsString>) {
    let (executable, mut args) = cli_command_from_config(executable, cli_path, python);
    if let Some(command) = args.last_mut() {
        *command = "verify-local".into();
    }
    args.extend([
        "--dashboard-url".into(), dashboard_url.into(),
        "--mcp-url".into(), mcp_url.into(),
    ]);
    (executable, args)
}

fn validate_local_acceptance_output(success: bool, stdout: &[u8]) -> Result<(), String> {
    if !success {
        return Err("AUREN local acceptance failed. Check the CLI output with auren verify-local.".into());
    }
    let report: serde_json::Value = serde_json::from_slice(stdout)
        .map_err(|_| "AUREN local acceptance returned an invalid report".to_string())?;
    if report.get("status").and_then(serde_json::Value::as_str) != Some("ready")
        || report.get("read_only").and_then(serde_json::Value::as_bool) != Some(true)
    {
        return Err("AUREN local acceptance did not confirm a ready, read-only result".into());
    }
    Ok(())
}

fn verify_local_endpoints(dashboard_url: &str, mcp_url: &str) -> Result<(), String> {
    let (executable, args) = verification_command_from_config(
        env::var_os("AUREN_EXECUTABLE"),
        env::var_os("AUREN_CLI_PATH"),
        env::var_os("AUREN_PYTHON_EXECUTABLE"),
        dashboard_url,
        mcp_url,
    );
    let output = Command::new(executable)
        .args(args)
        .output()
        .map_err(|_| "could not run AUREN's read-only local acceptance check".to_string())?;
    validate_local_acceptance_output(output.status.success(), &output.stdout)
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
    if child_slot.is_some() {
        let process_status = child_slot.as_mut().expect("child presence was checked")
            .try_wait().map_err(|_| "cannot inspect the AUREN CLI process")?;
        if process_status.is_none() {
            if let Some(info) = info_slot.as_ref() {
                if info.project_root == root.to_string_lossy().as_ref() {
                    return Ok(info.clone());
                }
                return Err("AUREN is already running for another project. Stop it before switching projects.".into());
            }
            return Err("AUREN is already starting. Wait for startup to finish.".into());
        }
        child_slot.take();
        *info_slot = None;
    }

    let (executable, mut command_args) = cli_command();
    command_args.extend([
        "--project-root".into(), root.as_os_str().to_os_string(),
        "--dashboard-port".into(), "0".into(),
        "--mcp-port".into(), "0".into(),
        "--no-connect".into(), "--no-browser".into(), "--no-chatgpt".into(),
    ]);
    let mut child = Command::new(executable)
        .args(command_args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| "could not start AUREN. Add the auren launcher to PATH, set AUREN_EXECUTABLE, or set AUREN_CLI_PATH and AUREN_PYTHON_EXECUTABLE.".to_string())?;

    let stdout = match child.stdout.take() {
        Some(stdout) => stdout,
        None => {
            terminate(&mut child);
            return Err("AUREN CLI stdout was unavailable".into());
        }
    };
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

    if let Err(error) = verify_local_endpoints(&dashboard_url, &mcp_url) {
        terminate(&mut child);
        return Err(error);
    }

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
fn choose_project_folder() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("Choose an AUREN project folder")
        .pick_folder()
        .map(|path| path.to_string_lossy().into_owned())
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeStatus {
    running: bool,
    info: Option<LocalRuntimeInfo>,
}

#[tauri::command]
fn runtime_status(state: State<'_, RuntimeState>) -> Result<RuntimeStatus, String> {
    let mut child = state.child.lock().map_err(|_| "runtime process lock is unavailable")?;
    let mut info = state.info.lock().map_err(|_| "runtime state lock is unavailable")?;
    let exited = match child.as_mut() {
        Some(process) => process.try_wait().map_err(|_| "cannot inspect the AUREN CLI process")?.is_some(),
        None => false,
    };
    if exited {
        child.take();
        *info = None;
    }
    Ok(RuntimeStatus {
        running: child.is_some(),
        info: info.clone(),
    })
}

#[tauri::command]
fn stop_local(state: State<'_, RuntimeState>) -> Result<(), String> {
    stop_runtime(state.inner())
}

fn main() {
    tauri::Builder::default()
        .manage(RuntimeState::default())
        .invoke_handler(tauri::generate_handler![start_local, stop_local, choose_project_folder, runtime_status])
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
    use super::{endpoint_from_line, validate_local_acceptance_output, verification_command_from_config};

    #[test]
    fn prefers_an_explicit_executable_and_supports_python_cli_paths() {
        use std::ffi::OsString;
        let (exe, args) = super::cli_command_from_config(
            Some(OsString::from("C:\\tools\\auren.exe")),
            Some(OsString::from("C:\\AUREN\\auren_cli.py")),
            Some(OsString::from("python.exe")),
        );
        assert_eq!(exe, OsString::from("C:\\tools\\auren.exe"));
        assert_eq!(args, vec![OsString::from("up")]);

        let (python, args) = super::cli_command_from_config(
            None,
            Some(OsString::from("C:\\AUREN\\auren_cli.py")),
            Some(OsString::from("python.exe")),
        );
        assert_eq!(python, OsString::from("python.exe"));
        assert_eq!(args, vec![
            OsString::from("C:\\AUREN\\auren_cli.py"),
            OsString::from("up"),
        ]);
    }

    #[test]
    fn builds_read_only_acceptance_command_for_installed_and_checkout_launchers() {
        use std::ffi::OsString;
        let (exe, args) = verification_command_from_config(
            Some(OsString::from("auren.exe")),
            None,
            None,
            "http://127.0.0.1:43120/control-center",
            "http://127.0.0.1:43121/mcp",
        );
        assert_eq!(exe, OsString::from("auren.exe"));
        assert_eq!(args, vec![
            OsString::from("verify-local"),
            OsString::from("--dashboard-url"),
            OsString::from("http://127.0.0.1:43120/control-center"),
            OsString::from("--mcp-url"),
            OsString::from("http://127.0.0.1:43121/mcp"),
        ]);

        let (python, checkout_args) = verification_command_from_config(
            None,
            Some(OsString::from(r"C:\AUREN\auren_cli.py")),
            Some(OsString::from("python.exe")),
            "http://127.0.0.1:43120/control-center",
            "http://127.0.0.1:43121/mcp",
        );
        assert_eq!(python, OsString::from("python.exe"));
        assert_eq!(checkout_args[0], OsString::from(r"C:\AUREN\auren_cli.py"));
        assert_eq!(checkout_args[1], OsString::from("verify-local"));
    }

    #[test]
    fn accepts_only_a_successful_ready_read_only_report() {
        let report = br#"{"status":"ready","read_only":true}"#;
        assert!(validate_local_acceptance_output(true, report).is_ok());
        assert!(validate_local_acceptance_output(false, report).is_err());
    }

    #[test]
    fn rejects_invalid_or_non_ready_local_acceptance_reports() {
        assert!(validate_local_acceptance_output(true, b"not-json").is_err());
        assert!(validate_local_acceptance_output(true, br#"{"status":"blocked","read_only":true}"#).is_err());
        assert!(validate_local_acceptance_output(true, br#"{"status":"ready","read_only":false}"#).is_err());
        assert!(validate_local_acceptance_output(true, br#"{"status":"ready"}"#).is_err());
    }

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
