use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager, State};

use crate::AppState;

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(default)]
pub struct Config {
    pub server_url: String,
    pub pcllm_dir: String,
    pub llm_port: u16,
    pub autostart_llm: bool,
    pub autostart_compute: bool,
    pub close_to_tray: bool,
    pub last_view: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            server_url: String::new(),
            pcllm_dir: String::new(),
            llm_port: 8099,
            autostart_llm: false,
            autostart_compute: false,
            close_to_tray: true,
            last_view: "dashboard".into(),
        }
    }
}

pub fn config_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|d| d.join("config.json"))
        .map_err(|e| e.to_string())
}

pub fn load(app: &AppHandle) -> Config {
    let Ok(path) = config_path(app) else {
        return Config::default();
    };
    match std::fs::read_to_string(&path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_default(),
        Err(_) => {
            let cfg = Config {
                pcllm_dir: detect_pcllm_dir().unwrap_or_default(),
                ..Config::default()
            };
            let _ = write(&path, &cfg);
            cfg
        }
    }
}

fn write(path: &Path, cfg: &Config) -> Result<(), String> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let text = serde_json::to_string_pretty(cfg).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| e.to_string())
}

fn detect_pcllm_dir() -> Option<String> {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        starts.push(exe);
    }
    starts.push(PathBuf::from(env!("CARGO_MANIFEST_DIR")));
    starts
        .iter()
        .flat_map(|p| p.ancestors())
        .map(|a| a.join("tools").join("pc-llm"))
        .find(|c| c.join("start-llm.ps1").is_file())
        .map(|c| c.to_string_lossy().trim_start_matches(r"\?\").to_string())
}

#[tauri::command]
pub fn config_get(state: State<AppState>) -> Config {
    state.config.lock().unwrap().clone()
}

#[tauri::command]
pub fn config_set(app: AppHandle, state: State<AppState>, config: Config) -> Result<Config, String> {
    let mut config = config;
    if config.llm_port == 0 {
        return Err("LLM port must be between 1 and 65535".into());
    }
    config.server_url = config.server_url.trim().trim_end_matches('/').to_string();
    config.pcllm_dir = config.pcllm_dir.trim().to_string();
    write(&config_path(&app)?, &config)?;
    *state.config.lock().unwrap() = config.clone();
    Ok(config)
}

#[tauri::command]
pub fn token_get() -> Result<Option<String>, String> {
    match entry()?.get_password() {
        Ok(t) => Ok(Some(t)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("Credential Manager: {e}")),
    }
}

#[tauri::command]
pub fn token_set(token: String) -> Result<(), String> {
    let entry = entry()?;
    let token = token.trim();
    if token.is_empty() {
        return match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(format!("Credential Manager: {e}")),
        };
    }
    entry
        .set_password(token)
        .map_err(|e| format!("Credential Manager: {e}"))
}

fn entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new("Saved", "session").map_err(|e| format!("Credential Manager: {e}"))
}

fn compute_entry() -> Result<keyring::Entry, String> {
    keyring::Entry::new("Saved", "compute_token").map_err(|e| format!("Credential Manager: {e}"))
}

pub fn compute_token() -> Result<Option<String>, String> {
    match compute_entry()?.get_password() {
        Ok(t) => Ok(Some(t)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("Credential Manager: {e}")),
    }
}

#[tauri::command]
pub fn compute_token_set(token: String) -> Result<(), String> {
    let token = token.trim();
    if token.is_empty() {
        return Err("Empty agent token".into());
    }
    compute_entry()?
        .set_password(token)
        .map_err(|e| format!("Credential Manager: {e}"))
}

#[tauri::command]
pub fn compute_token_clear() -> Result<(), String> {
    match compute_entry()?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(e) => Err(format!("Credential Manager: {e}")),
    }
}
