use serde::Serialize;
use std::collections::HashMap;
use std::fs::File;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::Ordering;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System};
use tauri::{AppHandle, Manager, State};

use crate::{config, llm, AppState};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
const SCRIPT: &str = "start-compute.ps1";
const GRACE: Duration = Duration::from_secs(5);
const CTRL_BREAK_EVENT: u32 = 1;

static SIGNAL_LOCK: Mutex<()> = Mutex::new(());

#[link(name = "kernel32")]
extern "system" {
    fn AttachConsole(pid: u32) -> i32;
    fn FreeConsole() -> i32;
    fn GenerateConsoleCtrlEvent(event: u32, group: u32) -> i32;
    fn SetConsoleCtrlHandler(handler: *const (), add: i32) -> i32;
}

#[derive(Serialize, Debug, Default)]
pub struct ComputeStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub llm_healthy: bool,
    pub has_token: bool,
    pub started_by_app: bool,
    pub user_stopped: bool,
}

fn snapshot() -> Option<System> {
    let mut sys = System::new().ok()?;
    sys.refresh_processes_specifics(ProcessesToUpdate::All, true, ProcessRefreshKind::everything());
    Some(sys)
}

fn launchers(sys: &System) -> Vec<u32> {
    sys.processes()
        .iter()
        .filter(|(_, p)| {
            let n = p.name().to_string_lossy().to_ascii_lowercase();
            (n == "powershell.exe" || n == "pwsh.exe")
                && p.cmd().iter().any(|a| a.to_string_lossy().to_ascii_lowercase().contains(SCRIPT))
        })
        .map(|(pid, _)| pid.as_u32())
        .collect()
}

fn descendants(sys: &System, roots: &[u32]) -> Vec<u32> {
    let mut children: HashMap<u32, Vec<u32>> = HashMap::new();
    for (pid, p) in sys.processes() {
        if let Some(parent) = p.parent() {
            children.entry(parent.as_u32()).or_default().push(pid.as_u32());
        }
    }
    let mut out: Vec<u32> = roots.to_vec();
    let mut i = 0;
    while i < out.len() {
        if let Some(kids) = children.get(&out[i]) {
            for k in kids {
                if !out.contains(k) {
                    out.push(*k);
                }
            }
        }
        i += 1;
    }
    out
}

pub fn running() -> bool {
    snapshot().is_some_and(|s| !launchers(&s).is_empty())
}

fn alive(pids: &[u32]) -> Vec<u32> {
    let Ok(mut sys) = System::new() else { return Vec::new() };
    let ids: Vec<Pid> = pids.iter().map(|p| Pid::from_u32(*p)).collect();
    sys.refresh_processes_specifics(ProcessesToUpdate::Some(&ids), true, ProcessRefreshKind::nothing());
    pids.iter().copied().filter(|p| sys.process(Pid::from_u32(*p)).is_some()).collect()
}

fn ctrl_break(group: u32) {
    let _guard = SIGNAL_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    unsafe {
        FreeConsole();
        if AttachConsole(group) != 0 {
            SetConsoleCtrlHandler(std::ptr::null(), 1);
            GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, group);
            FreeConsole();
            SetConsoleCtrlHandler(std::ptr::null(), 0);
        }
    }
}

fn stop_tree(roots: &[u32], extra: &[u32], grace: Duration) {
    let Some(sys) = snapshot() else { return };
    let mut tree = descendants(&sys, roots);
    for e in extra {
        if !tree.contains(e) {
            tree.push(*e);
        }
    }
    drop(sys);
    for r in roots {
        ctrl_break(*r);
    }
    let end = Instant::now() + grace;
    while Instant::now() < end && !alive(&tree).is_empty() {
        std::thread::sleep(Duration::from_millis(200));
    }
    let left = alive(&tree);
    if left.is_empty() {
        return;
    }
    let Ok(mut sys) = System::new() else { return };
    let ids: Vec<Pid> = left.iter().map(|p| Pid::from_u32(*p)).collect();
    sys.refresh_processes_specifics(ProcessesToUpdate::Some(&ids), true, ProcessRefreshKind::nothing());
    for id in &ids {
        if let Some(p) = sys.process(*id) {
            p.kill();
        }
    }
    let end = Instant::now() + Duration::from_secs(3);
    while Instant::now() < end && !alive(&left).is_empty() {
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn spawn(script: &Path, env: &[(&str, &str)], log: &Path) -> Result<u32, String> {
    if let Some(parent) = log.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let out = File::create(log).map_err(|e| e.to_string())?;
    let err = out.try_clone().map_err(|e| e.to_string())?;
    let mut cmd = Command::new("powershell.exe");
    cmd.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(script)
        .stdin(Stdio::null())
        .stdout(out)
        .stderr(err)
        .creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
    for (k, v) in env {
        cmd.env(k, v);
    }
    cmd.spawn()
        .map(|c| c.id())
        .map_err(|e| format!("Could not start powershell.exe: {e}"))
}

fn log_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|d| d.join("compute.log"))
        .map_err(|e| e.to_string())
}

fn script_path(pcllm_dir: &str) -> Result<PathBuf, String> {
    if pcllm_dir.is_empty() {
        return Err("Path to tools/pc-llm is not set".into());
    }
    let dir = Path::new(pcllm_dir)
        .parent()
        .ok_or("Path to tools/pc-llm has no parent folder")?
        .join("pc-compute");
    let script = dir.join(SCRIPT);
    if !script.is_file() {
        return Err(format!("{SCRIPT} not found in {}", dir.display()));
    }
    Ok(script)
}

pub fn status(state: &AppState) -> ComputeStatus {
    let port = state.config.lock().unwrap().llm_port;
    let pid = snapshot().and_then(|s| launchers(&s).first().copied());
    ComputeStatus {
        running: pid.is_some(),
        pid,
        llm_healthy: llm::health_ok(port),
        has_token: matches!(config::compute_token(), Ok(Some(_))),
        started_by_app: state.compute_started.load(Ordering::Relaxed),
        user_stopped: state.compute_user_stopped.load(Ordering::Relaxed),
    }
}

pub fn start(app: &AppHandle, state: &AppState) -> Result<(), String> {
    let (pcllm_dir, url, port) = {
        let c = state.config.lock().unwrap();
        (c.pcllm_dir.clone(), c.server_url.clone(), c.llm_port)
    };
    let script = script_path(&pcllm_dir)?;
    if url.is_empty() {
        return Err("Server URL is not set".into());
    }
    let token = config::compute_token()?.ok_or("The agent token is missing")?;
    if running() {
        return Err("PC compute is already running".into());
    }
    if llm::status(state).running {
        llm::stop(state).map_err(|e| format!("Could not stop the plain GPU server first: {e}"))?;
        let end = Instant::now() + Duration::from_secs(10);
        while llm::listener_pid(port).is_some() {
            if Instant::now() > end {
                return Err(format!("The plain GPU server did not release port {port}"));
            }
            std::thread::sleep(Duration::from_millis(300));
        }
    }
    spawn(&script, &[("SAVED_URL", &url), ("SAVED_TOKEN", &token)], &log_path(app)?)?;
    state.compute_started.store(true, Ordering::Relaxed);
    state.compute_user_stopped.store(false, Ordering::Relaxed);
    Ok(())
}

pub fn stop(state: &AppState) -> Result<(), String> {
    let port = state.config.lock().unwrap().llm_port;
    let sys = snapshot().ok_or("Could not list processes")?;
    let roots = launchers(&sys);
    if roots.is_empty() {
        return Err("PC compute is not running".into());
    }
    let mut extra = Vec::new();
    if let Some(pid) = llm::listener_pid(port) {
        if sys.process(Pid::from_u32(pid)).is_some_and(|p| llm::is_named(p, llm::SERVER_EXE)) {
            extra.push(pid);
        }
    }
    drop(sys);
    state.compute_user_stopped.store(true, Ordering::Relaxed);
    stop_tree(&roots, &extra, GRACE);
    state.compute_started.store(false, Ordering::Relaxed);
    Ok(())
}

pub fn stop_if_started(state: &AppState) {
    if state.compute_started.load(Ordering::Relaxed) {
        let _ = stop(state);
    }
}

#[tauri::command(async)]
pub fn compute_status(state: State<AppState>) -> ComputeStatus {
    status(&state)
}

#[tauri::command(async)]
pub fn compute_start(app: AppHandle, state: State<AppState>) -> Result<(), String> {
    start(&app, &state)
}

#[tauri::command(async)]
pub fn compute_stop(state: State<AppState>) -> Result<(), String> {
    stop(&state)
}

#[tauri::command(async)]
pub fn compute_log_tail(app: AppHandle, lines: usize) -> Result<String, String> {
    llm::tail(&log_path(&app)?, lines)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::SystemTime;

    fn scratch(tag: &str) -> PathBuf {
        let n = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH).unwrap().as_nanos();
        let d = std::env::temp_dir().join(format!("saved-compute-test-{tag}-{n}"));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    fn wait_for(path: &Path, needle: &str, secs: u64) -> bool {
        let end = Instant::now() + Duration::from_secs(secs);
        while Instant::now() < end {
            if std::fs::read_to_string(path).is_ok_and(|t| t.contains(needle)) {
                return true;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        false
    }

    fn write_standin(dir: &Path, ignore_break: bool) -> PathBuf {
        let agent = dir.join("agent.py");
        let handler = if ignore_break {
            "signal.signal(signal.SIGBREAK, signal.SIG_IGN)\n"
        } else {
            "def bye(*a):\n    open(sys.argv[1], 'w').write('released')\n    sys.exit(0)\nsignal.signal(signal.SIGBREAK, bye)\n"
        };
        std::fs::write(
            &agent,
            format!("import signal, sys, time\n{handler}print('agent up', flush=True)\nwhile True:\n    time.sleep(0.2)\n"),
        )
        .unwrap();
        let script = dir.join(SCRIPT);
        std::fs::write(
            &script,
            format!(
                "Write-Output \"url=$env:SAVED_URL SAVED_TOKEN=$env:SAVED_TOKEN\"\n\
                 Write-Output \"Authorization: Bearer $env:SAVED_TOKEN\"\n\
                 $ping = Start-Process -NoNewWindow -PassThru python -ArgumentList '-c','import time; time.sleep(600)' -RedirectStandardOutput '{null}'\n\
                 Write-Output \"ping=$($ping.Id)\"\n\
                 python -I '{agent}' '{marker}'\n",
                null = dir.join("ping.out").display(),
                agent = agent.display(),
                marker = dir.join("released.txt").display(),
            ),
        )
        .unwrap();
        script
    }

    fn run_case(ignore_break: bool) -> (PathBuf, Duration, Vec<u32>) {
        let dir = scratch(if ignore_break { "forced" } else { "graceful" });
        let script = write_standin(&dir, ignore_break);
        let log = dir.join("compute.log");
        let root = spawn(&script, &[("SAVED_URL", "http://example.test"), ("SAVED_TOKEN", "tok_SECRET123")], &log).unwrap();
        assert!(wait_for(&log, "agent up", 30), "stand-in did not start");
        let sys = snapshot().unwrap();
        assert!(launchers(&sys).contains(&root), "launcher not detected");
        let tree = descendants(&sys, &[root]);
        assert!(tree.len() >= 3, "expected powershell, a sleeper and the agent in the tree, got {tree:?}");
        drop(sys);
        let t = Instant::now();
        stop_tree(&[root], &[], Duration::from_secs(3));
        (dir, t.elapsed(), tree)
    }

    #[test]
    fn graceful_stop_lets_the_agent_release_and_leaves_no_orphans() {
        let (dir, took, tree) = run_case(false);
        assert!(dir.join("released.txt").is_file(), "agent did not get CTRL_BREAK");
        assert!(took < Duration::from_secs(3), "stop was not graceful: {took:?}");
        assert!(alive(&tree).is_empty(), "orphans left: {:?}", alive(&tree));
        let shown = llm::redact(&std::fs::read_to_string(dir.join("compute.log")).unwrap());
        assert!(!shown.contains("tok_SECRET123"));
        assert!(shown.contains("SAVED_TOKEN=***"));
        assert!(shown.contains("Bearer ***"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn stubborn_agent_is_force_killed_after_the_grace_period() {
        let (dir, took, tree) = run_case(true);
        assert!(!dir.join("released.txt").exists());
        assert!(took >= Duration::from_secs(3), "forced path was not used: {took:?}");
        assert!(alive(&tree).is_empty(), "orphans left: {:?}", alive(&tree));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn script_path_is_a_sibling_of_pc_llm() {
        let d = scratch("path");
        std::fs::create_dir_all(d.join("pc-llm")).unwrap();
        std::fs::create_dir_all(d.join("pc-compute")).unwrap();
        let llm_dir = d.join("pc-llm").to_string_lossy().into_owned();
        assert!(script_path(&llm_dir).is_err());
        std::fs::write(d.join("pc-compute").join(SCRIPT), "").unwrap();
        assert_eq!(script_path(&llm_dir).unwrap(), d.join("pc-compute").join(SCRIPT));
        assert!(script_path("").is_err());
        let _ = std::fs::remove_dir_all(d);
    }
}
