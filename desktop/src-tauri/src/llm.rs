use serde::Serialize;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::net::{SocketAddr, TcpStream};
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::atomic::Ordering;
use std::time::Duration;
use sysinfo::{Pid, Process, ProcessRefreshKind, ProcessesToUpdate, System};
use tauri::{AppHandle, Manager, State};

use crate::AppState;

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const DETACHED_PROCESS: u32 = 0x0000_0008;
const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
pub(crate) const SERVER_EXE: &str = "llama-server.exe";
const SCRIPT: &str = "start-llm.ps1";

#[derive(Serialize, Debug, Default)]
pub struct LlmStatus {
    pub running: bool,
    pub healthy: bool,
    pub pid: Option<u32>,
    pub model_file: Option<String>,
    pub started_by_app: bool,
    pub user_stopped: bool,
}

pub fn model_file_from_args(args: &[String]) -> Option<String> {
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        let value = match arg.as_str() {
            "-m" | "--model" => it.next().map(String::as_str),
            a => a.strip_prefix("--model=").or_else(|| a.strip_prefix("-m=")),
        };
        if let Some(v) = value {
            let name = v.trim_matches('"').rsplit(['\\', '/']).next().unwrap_or("");
            return (!name.is_empty()).then(|| name.to_string());
        }
    }
    None
}

pub fn parse_netstat_listener(output: &str, port: u16) -> Option<u32> {
    let suffix = format!(":{port}");
    output.lines().find_map(|line| {
        let cols: Vec<&str> = line.split_whitespace().collect();
        if cols.len() < 5 || !cols[0].eq_ignore_ascii_case("TCP") || !cols[3].eq_ignore_ascii_case("LISTENING") {
            return None;
        }
        if cols[1].ends_with(&suffix) {
            cols[4].parse().ok()
        } else {
            None
        }
    })
}

pub fn redact(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    loop {
        let hit = ["LLM_REMOTE_KEY=", "--api-key ", "SAVED_TOKEN=", "Bearer ", "bearer "]
            .iter()
            .filter_map(|m| rest.find(m).map(|i| (i, m.len())))
            .min();
        let Some((i, len)) = hit else { break };
        out.push_str(&rest[..i + len]);
        out.push_str("***");
        let after = &rest[i + len..];
        let end = after.find(char::is_whitespace).unwrap_or(after.len());
        rest = &after[end..];
    }
    out.push_str(rest);
    out
}

pub(crate) fn health_ok(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(800)) else {
        return false;
    };
    let t = Some(Duration::from_millis(1500));
    let _ = s.set_read_timeout(t);
    let _ = s.set_write_timeout(t);
    if s.write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").is_err() {
        return false;
    }
    let mut buf = [0u8; 16];
    let n = s.read(&mut buf).unwrap_or(0);
    buf[..n].starts_with(b"HTTP/1.1 200") || buf[..n].starts_with(b"HTTP/1.0 200")
}

pub(crate) fn listener_pid(port: u16) -> Option<u32> {
    let out = Command::new("netstat")
        .args(["-ano", "-p", "TCP"])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .ok()?;
    parse_netstat_listener(&String::from_utf8_lossy(&out.stdout), port)
}

pub(crate) fn inspect(sys: &mut System, pid: u32) -> Option<&Process> {
    let pid = Pid::from_u32(pid);
    sys.refresh_processes_specifics(ProcessesToUpdate::Some(&[pid]), true, ProcessRefreshKind::everything());
    sys.process(pid)
}

pub(crate) fn is_named(p: &Process, name: &str) -> bool {
    p.name().to_string_lossy().eq_ignore_ascii_case(name)
}

fn is_launcher(p: &Process) -> bool {
    let n = p.name().to_string_lossy().to_ascii_lowercase();
    (n == "powershell.exe" || n == "pwsh.exe")
        && p.cmd().iter().any(|a| a.to_string_lossy().to_ascii_lowercase().contains(SCRIPT))
}

fn status_for(port: u16, state: &AppState) -> LlmStatus {
    let mut st = LlmStatus {
        healthy: health_ok(port),
        started_by_app: state.llm_started.load(Ordering::Relaxed),
        user_stopped: state.llm_user_stopped.load(Ordering::Relaxed),
        ..LlmStatus::default()
    };
    if let Some(pid) = listener_pid(port) {
        if let Ok(mut sys) = System::new() {
            if let Some(p) = inspect(&mut sys, pid) {
                if is_named(p, SERVER_EXE) {
                    st.running = true;
                    st.pid = Some(pid);
                    let args: Vec<String> = p.cmd().iter().map(|a| a.to_string_lossy().into_owned()).collect();
                    st.model_file = model_file_from_args(&args);
                }
            }
        }
    }
    st
}

fn log_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|d| d.join("llm.log"))
        .map_err(|e| e.to_string())
}

pub fn start(app: &AppHandle, state: &AppState) -> Result<(), String> {
    let (dir, port) = {
        let c = state.config.lock().unwrap();
        (c.pcllm_dir.clone(), c.llm_port)
    };
    if dir.is_empty() {
        return Err("Path to tools/pc-llm is not set".into());
    }
    let script = PathBuf::from(&dir).join(SCRIPT);
    if !script.is_file() {
        return Err(format!("{SCRIPT} not found in {dir}"));
    }
    if crate::compute::running() {
        return Err("PC compute is running and owns the GPU server. Stop it first.".into());
    }
    if status_for(port, state).running {
        return Err(format!("llama-server is already running on port {port}"));
    }
    let log = log_path(app)?;
    if let Some(parent) = log.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let out = File::create(&log).map_err(|e| e.to_string())?;
    let err = out.try_clone().map_err(|e| e.to_string())?;
    Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(&script)
        .args(["-Port", &port.to_string()])
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err)
        .creation_flags(DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        .spawn()
        .map_err(|e| format!("Could not start powershell.exe: {e}"))?;
    state.llm_started.store(true, Ordering::Relaxed);
    state.llm_user_stopped.store(false, Ordering::Relaxed);
    Ok(())
}

pub fn stop(state: &AppState) -> Result<(), String> {
    if crate::compute::running() {
        return Err("PC compute is running and owns the GPU server. Stop PC compute instead.".into());
    }
    let port = state.config.lock().unwrap().llm_port;
    let pid = listener_pid(port).ok_or_else(|| format!("Nothing is listening on port {port}"))?;
    let mut sys = System::new().map_err(|e| e.to_string())?;
    let parent = {
        let p = inspect(&mut sys, pid).ok_or("The listening process is gone")?;
        if !is_named(p, SERVER_EXE) {
            return Err(format!("Port {port} is held by {}, not {SERVER_EXE}", p.name().to_string_lossy()));
        }
        p.parent()
    };
    state.llm_user_stopped.store(true, Ordering::Relaxed);
    if let Some(parent) = parent {
        if let Some(pp) = inspect(&mut sys, parent.as_u32()) {
            if is_launcher(pp) {
                pp.kill();
            }
        }
    }
    if let Some(p) = inspect(&mut sys, pid) {
        p.kill();
    }
    state.llm_started.store(false, Ordering::Relaxed);
    Ok(())
}

pub fn status(state: &AppState) -> LlmStatus {
    let port = state.config.lock().unwrap().llm_port;
    status_for(port, state)
}

#[tauri::command(async)]
pub fn llm_status(state: State<AppState>) -> LlmStatus {
    status(&state)
}

#[tauri::command(async)]
pub fn llm_start(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    start(&app, &state)
}

#[tauri::command(async)]
pub fn llm_stop(state: State<AppState>) -> Result<(), String> {
    stop(&state)
}

pub(crate) fn tail(path: &PathBuf, lines: usize) -> Result<String, String> {
    let Ok(mut f) = File::open(path) else {
        return Ok(String::new());
    };
    let len = f.metadata().map_err(|e| e.to_string())?.len();
    f.seek(SeekFrom::Start(len.saturating_sub(256 * 1024))).map_err(|e| e.to_string())?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    let text = redact(&String::from_utf8_lossy(&buf));
    let all: Vec<&str> = text.lines().collect();
    Ok(all[all.len().saturating_sub(lines.clamp(1, 2000))..].join("\n"))
}

#[tauri::command(async)]
pub fn llm_log_tail(app: AppHandle, lines: usize) -> Result<String, String> {
    tail(&log_path(&app)?, lines)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(s: &[&str]) -> Vec<String> {
        s.iter().map(|a| a.to_string()).collect()
    }

    #[test]
    fn extracts_model_file_from_short_flag() {
        let a = args(&[
            r"C:\llama\llama-server.exe", "-m", r"D:\models\Qwen3.5-9B-Q4_K_M.gguf",
            "--mmproj", r"D:\models\mmproj-F16.gguf", "-ngl", "99", "--api-key", "s3cret",
        ]);
        assert_eq!(model_file_from_args(&a).as_deref(), Some("Qwen3.5-9B-Q4_K_M.gguf"));
    }

    #[test]
    fn extracts_model_file_from_long_and_equals_forms() {
        assert_eq!(model_file_from_args(&args(&["x", "--model", "/a/b/m.gguf"])).as_deref(), Some("m.gguf"));
        assert_eq!(model_file_from_args(&args(&["x", "--model=C:\\a\\n.gguf"])).as_deref(), Some("n.gguf"));
        assert_eq!(model_file_from_args(&args(&["x", "--port", "8099"])), None);
        assert_eq!(model_file_from_args(&args(&["x", "-m"])), None);
    }

    #[test]
    fn api_key_never_reaches_the_status() {
        let a = args(&["llama-server.exe", "--api-key", "TOPSECRET123", "-m", r"D:\m\model.gguf"]);
        let st = LlmStatus {
            running: true,
            healthy: true,
            pid: Some(1),
            model_file: model_file_from_args(&a),
            ..LlmStatus::default()
        };
        let json = serde_json::to_string(&st).unwrap();
        assert!(!json.contains("TOPSECRET123"));
        assert!(!json.contains("api-key"));
        assert!(json.contains("model.gguf"));
        let only_key = args(&["llama-server.exe", "--api-key", "TOPSECRET123"]);
        assert_eq!(model_file_from_args(&only_key), None);
    }

    #[test]
    fn finds_the_listener_pid() {
        let out = "\
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:80             0.0.0.0:0              LISTENING       4
  TCP    0.0.0.0:8099           0.0.0.0:0              LISTENING       12345
  TCP    127.0.0.1:50999        127.0.0.1:8099         ESTABLISHED     777
  TCP    [::]:18099             [::]:0                 LISTENING       999
";
        assert_eq!(parse_netstat_listener(out, 8099), Some(12345));
        assert_eq!(parse_netstat_listener(out, 80), Some(4));
        assert_eq!(parse_netstat_listener(out, 9), None);
    }

    #[test]
    fn redacts_keys_in_log_text() {
        let text = "Put this in the .env as: LLM_REMOTE_KEY=abcd2345 ok\nrun --api-key hunter2 --no-webui\nplain line";
        let r = redact(text);
        assert!(!r.contains("abcd2345"));
        assert!(!r.contains("hunter2"));
        assert!(r.contains("LLM_REMOTE_KEY=*** ok"));
        assert!(r.contains("--api-key *** --no-webui"));
        assert!(r.contains("plain line"));
        let t = redact("env SAVED_TOKEN=tok123 x\nAuthorization: Bearer tok456 ok");
        assert!(!t.contains("tok123") && !t.contains("tok456"));
        assert!(t.contains("SAVED_TOKEN=*** x") && t.contains("Bearer *** ok"));
    }

    #[test]
    fn health_check_reads_the_status_line() {
        use std::net::TcpListener;
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        let h = std::thread::spawn(move || {
            for reply in ["HTTP/1.1 200 OK\r\n\r\n{}", "HTTP/1.1 503 Loading\r\n\r\n"] {
                let (mut c, _) = l.accept().unwrap();
                let mut b = [0u8; 256];
                let _ = c.read(&mut b);
                c.write_all(reply.as_bytes()).unwrap();
            }
        });
        assert!(health_ok(port));
        assert!(!health_ok(port));
        h.join().unwrap();
    }
}
