mod compute;
mod config;
mod gpu;
mod llm;

use config::Config;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, State, WebviewWindowBuilder, Wry};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

pub struct AppState {
    pub config: Mutex<Config>,
    pub llm_started: AtomicBool,
    pub llm_user_stopped: AtomicBool,
    pub compute_started: AtomicBool,
    pub compute_user_stopped: AtomicBool,
    pub hybrid: AtomicBool,
}

struct TrayParts {
    tray: TrayIcon,
    gpu: MenuItem<Wry>,
    worker: MenuItem<Wry>,
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn toast(app: &AppHandle, title: &str, body: &str) {
    let _ = app.notification().builder().title(title).body(body).show();
}

#[tauri::command]
fn tray_update(state: State<AppState>, parts: State<TrayParts>, tooltip: String, gpu_running: bool, worker_paused: bool, hybrid: bool) {
    state.hybrid.store(hybrid, Ordering::Relaxed);
    let _ = parts.tray.set_tooltip(Some(tooltip));
    let label = match (hybrid, gpu_running) {
        (true, true) => "Stop PC compute",
        (true, false) => "Start PC compute",
        (false, true) => "Stop GPU server",
        (false, false) => "Start GPU server",
    };
    let _ = parts.gpu.set_text(label);
    let _ = parts.worker.set_text(if worker_paused { "Resume worker" } else { "Pause worker" });
}

#[tauri::command]
fn notify(app: AppHandle, title: String, body: String) {
    toast(&app, &title, &body);
}

#[tauri::command]
fn open_external(app: AppHandle, state: State<AppState>, url: String) -> Result<(), String> {
    let base = state.config.lock().unwrap().server_url.clone();
    if base.is_empty() || !(url == base || url.starts_with(&format!("{base}/"))) {
        return Err("Only the configured server URL can be opened".into());
    }
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

fn toggle_gpu(app: &AppHandle) {
    if app.state::<AppState>().hybrid.load(Ordering::Relaxed) {
        let _ = app.emit_to("main", "tray-action", "toggle-compute");
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        let state = app.state::<AppState>();
        let result = if llm::status(&state).running {
            llm::stop(&state)
        } else {
            llm::start(&app, &state)
        };
        if let Err(e) = result {
            toast(&app, "Saved: GPU server", &e);
        }
    });
}

fn build_tray(app: &AppHandle) -> tauri::Result<TrayParts> {
    let show = MenuItem::with_id(app, "show", "Show", true, None::<&str>)?;
    let gpu = MenuItem::with_id(app, "gpu", "Start GPU server", true, None::<&str>)?;
    let worker = MenuItem::with_id(app, "worker", "Pause worker", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&show, &sep, &gpu, &worker, &sep, &quit])?;
    let tray = TrayIconBuilder::with_id("main")
        .icon(app.default_window_icon().cloned().ok_or(tauri::Error::WindowNotFound)?)
        .tooltip("Saved")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_main(app),
            "gpu" => toggle_gpu(app),
            "worker" => {
                let _ = app.emit_to("main", "tray-action", "toggle-worker");
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(TrayParts { tray, gpu, worker })
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show_main(app)))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let handle = app.handle();
            let cfg = config::load(handle);
            let autostart = cfg.autostart_llm && !cfg.pcllm_dir.is_empty();
            app.manage(AppState {
                config: Mutex::new(cfg),
                llm_started: AtomicBool::new(false),
                llm_user_stopped: AtomicBool::new(false),
                compute_started: AtomicBool::new(false),
                compute_user_stopped: AtomicBool::new(false),
                hybrid: AtomicBool::new(false),
            });
            app.manage(build_tray(handle)?);
            for window_config in &app.config().app.windows {
                WebviewWindowBuilder::from_config(handle, window_config)?.build()?;
            }
            if autostart {
                let app = handle.clone();
                std::thread::spawn(move || {
                    let state = app.state::<AppState>();
                    if !llm::status(&state).running {
                        if let Err(e) = llm::start(&app, &state) {
                            toast(&app, "Saved: GPU server", &e);
                        }
                    }
                });
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let close_to_tray = window.app_handle().state::<AppState>().config.lock().unwrap().close_to_tray;
                if close_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            config::config_get,
            config::config_set,
            config::token_get,
            config::token_set,
            config::compute_token_set,
            config::compute_token_clear,
            compute::compute_status,
            compute::compute_start,
            compute::compute_stop,
            compute::compute_log_tail,
            gpu::gpu_stats,
            llm::llm_status,
            llm::llm_start,
            llm::llm_stop,
            llm::llm_log_tail,
            tray_update,
            notify,
            open_external,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Saved")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                compute::stop_if_started(&app.state::<AppState>());
            }
        });
}
