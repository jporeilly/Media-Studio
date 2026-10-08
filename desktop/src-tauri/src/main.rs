// Media Studio Enterprise - desktop shell.
//
// The app itself is unchanged: a FastAPI server serving the React SPA. This
// binary only starts that server (via boot.py, on the bundled Python), waits
// for it to answer /api/system/health, and points a webview at it. Everything
// the user sees is still the web UI, so the browser and desktop builds cannot
// drift apart.
//
// Paths are resolved through Tauri's path helpers, never hardcoded - the
// install root is not predictable and the app directory may be read-only.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod capture;
mod server;

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use tauri::{Manager, State};

use server::{last_server_output, Server};

/// Shared so the window-close handler can stop the server without borrowing
/// from the window - going through `State` there ties the borrow to a
/// temporary, which does not outlive the closure.
type SharedServer = Arc<Mutex<Option<Server>>>;

struct AppState {
    server: SharedServer,
}

/// Strip Windows' verbatim `\\?\` prefix.
///
/// Tauri's `resource_dir()` canonicalises, which on Windows yields an
/// extended-length path like `\\?\C:\Program Files\...`. Those are legal for
/// most file APIs, and the shell's own `is_file()` checks pass happily - which
/// is why diagnostics could report everything found while nothing worked.
///
/// They are NOT legal as a process WORKING DIRECTORY: `SetCurrentDirectory`
/// rejects the verbatim form, so `boot.py`'s `os.chdir()` raised and the server
/// died before uvicorn ever bound a port.
///
/// Only safe to strip for ordinary drive paths - a genuine UNC (`\\?\UNC\...`)
/// or a >260-char path still needs the prefix, so those are left alone.
fn strip_verbatim(p: &Path) -> PathBuf {
    let s = p.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        // Drive-letter paths only: "C:\..." - never \\?\UNC\server\share.
        let bytes = rest.as_bytes();
        let drive_path = bytes.len() >= 3
            && bytes[0].is_ascii_alphabetic()
            && bytes[1] == b':'
            && bytes[2] == b'\\';
        if drive_path && rest.len() < 250 {
            return PathBuf::from(rest);
        }
    }
    p.to_path_buf()
}

/// The app root: the directory holding main.py, api/, core/, utils/, services/
/// and boot.py.
///
/// Packaged: bundle.resources drops the app tree at `<resources>/app`.
/// Dev (`npm run tauri:dev`): walk up to the checkout root and use it in place,
/// so there is no build step between editing Python and seeing the change.
fn app_dir(handle: &tauri::AppHandle) -> PathBuf {
    let packaged = handle
        .path()
        .resource_dir()
        .ok()
        .map(|res| strip_verbatim(&res.join("app")));
    if let Some(p) = &packaged {
        if p.join("main.py").is_file() {
            return p.clone();
        }
    }
    // Dev build only: src-tauri -> desktop -> repo root. A release binary must
    // never fall back to the developer's checkout path baked in at compile time:
    // an incomplete install should report the packaged location it looked in.
    #[cfg(debug_assertions)]
    let fallback = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    #[cfg(not(debug_assertions))]
    let fallback = packaged.unwrap_or_else(|| PathBuf::from("app"));
    fallback
}

/// boot.py - staged beside the app tree, or taken from the checkout in dev.
/// Mirrors app_dir()'s packaged-then-checkout resolution deliberately: one rule,
/// applied twice, beats two rules that can disagree about which tree is live.
fn boot_py(handle: &tauri::AppHandle) -> PathBuf {
    let packaged = handle
        .path()
        .resource_dir()
        .ok()
        .map(|res| strip_verbatim(&res.join("app").join("boot.py")));
    if let Some(p) = &packaged {
        if p.is_file() {
            return p.clone();
        }
    }
    // Dev build only: src-tauri -> desktop/boot.py (see app_dir for why release
    // builds report the packaged path instead).
    #[cfg(debug_assertions)]
    let fallback = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("boot.py");
    #[cfg(not(debug_assertions))]
    let fallback = packaged.unwrap_or_else(|| PathBuf::from("app").join("boot.py"));
    fallback
}

/// Where the app writes its data. Media Studio's config/store resolve this as
/// `<app root>/data` (utils/config.py, api/store.py), so a packaged install
/// MUST be per-user/writable - the NSIS installer is `currentUser` for exactly
/// this reason. Reported here so "where did my projects go?" has an answer even
/// when the backend never started.
fn data_dir(handle: &tauri::AppHandle) -> PathBuf {
    app_dir(handle).join("data")
}

/// The splash page polls this until the server answers, then navigates to it.
#[tauri::command]
fn server_url(state: State<'_, AppState>) -> Option<String> {
    state.server.lock().ok()?.as_ref().map(|s| s.url())
}

/// The backend's output so far, for the splash to show WHILE it waits.
///
/// Deliberately separate from `diagnostics`: the splash polls this every few
/// hundred ms, and it means the startup screen shows what is really happening -
/// uvicorn's own "Started server process" / "Application startup complete" -
/// rather than a bar that moves whether or not anything is working.
#[tauri::command]
fn server_log() -> Vec<String> {
    last_server_output()
}

/// False once the backend process has exited. Lets the splash fail fast on a
/// dead server instead of waiting out a timeout meant for a slow one.
#[tauri::command]
fn server_alive(state: State<'_, AppState>) -> bool {
    let Ok(mut guard) = state.server.lock() else {
        return true; // cannot tell - assume alive rather than cry wolf
    };
    match guard.as_mut() {
        Some(srv) => !srv.exited().unwrap_or(false),
        None => false, // never started
    }
}

/// True once the backend answers /api/system/health. See server::http_ok for
/// why this cannot be a fetch() from the splash.
///
/// `async`: the probe can block for seconds (connect + read timeout), and a
/// plain sync command runs on the main thread - the splash would freeze for
/// exactly as long as the check it is animating.
#[tauri::command(async)]
fn server_ready(state: State<'_, AppState>) -> bool {
    let Ok(guard) = state.server.lock() else { return false };
    match guard.as_ref() {
        Some(srv) => server::http_ok(srv.port, "/api/system/health"),
        None => false,
    }
}

/// What this install is and where its data lives. Cheap, backend-independent
/// facts the splash can show at all times - the version answers the first
/// question on any failure ("which build is this?"), and it must not depend on
/// the backend answering, because the backend is what failed.
#[tauri::command]
fn env_report(handle: tauri::AppHandle) -> serde_json::Value {
    let data = data_dir(&handle);
    serde_json::json!({
        "version": handle.package_info().version.to_string(),
        "data_dir": data.to_string_lossy(),
        "data_dir_exists": data.is_dir(),
    })
}

/// Restart the backend in place.
///
/// Until now the only recovery from a failed start was closing the window and
/// relaunching - which is what everyone tries first anyway, so the app may as
/// well do it. A port already in use, an antivirus holding a file for a moment,
/// a service starting slowly: all clear on a second attempt, and none of them
/// deserve a reinstall.
///
/// `async` for the same reason as server_ready: kill + wait + spawn takes long
/// enough to freeze the splash if it ran on the main thread.
#[tauri::command(async)]
fn restart_server(handle: tauri::AppHandle, state: State<'_, AppState>) -> bool {
    let resource_dir = strip_verbatim(&handle.path().resource_dir().unwrap_or_default());
    let app_dir = app_dir(&handle);
    let boot_py = boot_py(&handle);

    let Ok(mut guard) = state.server.lock() else { return false };
    if let Some(srv) = guard.as_mut() {
        srv.stop();
    }
    *guard = None;

    match Server::start(&resource_dir, &boot_py, &app_dir) {
        Ok(srv) => {
            // A restart may land on a new port: the capture commands follow it.
            if let Err(e) = capture::pin_capability(&handle, srv.port) {
                eprintln!("{e}");
            }
            *guard = Some(srv);
            true
        }
        Err(e) => {
            eprintln!("restart failed: {e}");
            false
        }
    }
}

/// Open the data directory in Explorer. It is the answer to "where did my
/// projects/output go?", and typing the path by hand is nobody's idea of a good
/// time.
#[tauri::command]
fn open_state_dir(handle: tauri::AppHandle) -> String {
    let dir = data_dir(&handle);
    std::fs::create_dir_all(&dir).ok();
    dir.to_string_lossy().into_owned()
}

/// Surfaced on the splash when startup fails, so a dead backend reads as an
/// error message rather than a permanently blank window.
#[tauri::command]
fn diagnostics(handle: tauri::AppHandle) -> serde_json::Value {
    let dir = app_dir(&handle);
    serde_json::json!({
        "app_dir": dir.to_string_lossy(),
        "main_py_found": dir.join("main.py").is_file(),
        "boot_py": boot_py(&handle).to_string_lossy(),
        "boot_py_found": boot_py(&handle).is_file(),
        "data_dir": data_dir(&handle).to_string_lossy(),
        "vendored_python": handle
            .path()
            .resource_dir()
            .map(|r| strip_verbatim(&r).join("python").join("python.exe").is_file())
            .unwrap_or(false),
        // The last thing the backend said before dying. Without this a failed
        // start is a blank window and a shrug: every path check passes, because
        // the paths were never the problem.
        "server_log": last_server_output(),
    })
}

/// Everything a support email needs, as one block of text.
///
/// Built HERE rather than assembled in JavaScript so that the version, the OS
/// and the resolved paths cannot be omitted by a page that failed to load
/// properly - which, on a startup failure, is exactly the situation. Also
/// written to a file, because a 40-line traceback survives a paste badly and an
/// attachment does not.
#[tauri::command]
fn save_report(handle: tauri::AppHandle) -> serde_json::Value {
    let diag = diagnostics(handle.clone());
    let env = env_report(handle.clone());

    let mut out = String::new();
    out.push_str("Media Studio Enterprise - startup report\n");
    out.push_str("========================================\n\n");
    out.push_str(&format!("version   : {}\n", handle.package_info().version));
    out.push_str(&format!("os        : {} {}\n", std::env::consts::OS, std::env::consts::ARCH));
    out.push_str(&format!("exe       : {}\n",
        std::env::current_exe().map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_else(|_| "unknown".into())));
    out.push_str("\n-- what the shell resolved ------------------------\n");
    out.push_str(&serde_json::to_string_pretty(&diag).unwrap_or_default());
    out.push_str("\n\n-- this install ----------------------------------\n");
    out.push_str(&serde_json::to_string_pretty(&env).unwrap_or_default());
    out.push_str("\n\n-- backend output --------------------------------\n");
    let log = last_server_output();
    if log.is_empty() {
        out.push_str("(the backend produced no output at all)\n");
    } else {
        for line in log {
            out.push_str(&line);
            out.push('\n');
        }
    }

    // Into the DATA directory, which is writable by definition (the app writes
    // its SQLite and output there); a report that cannot be written is worse
    // than none.
    let path = data_dir(&handle).join("startup-report.txt");
    let written = std::fs::create_dir_all(data_dir(&handle))
        .and_then(|_| std::fs::write(&path, &out))
        .is_ok();

    serde_json::json!({
        "text": out,
        "path": path.to_string_lossy(),
        "written": written,
    })
}

fn main() {
    let shared: SharedServer = Arc::new(Mutex::new(None));
    let for_close = shared.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppState {
            server: shared.clone(),
        })
        .invoke_handler(tauri::generate_handler![
            server_url,
            server_log,
            server_alive,
            server_ready,
            env_report,
            diagnostics,
            save_report,
            restart_server,
            open_state_dir,
            // Screen capture (T3): monitors and windows in physical pixels,
            // and the region overlay. Declared in build.rs as well, so the
            // ACL can grant them to the UI's http origin (capabilities/).
            capture::capture_monitors,
            capture::capture_windows,
            capture::capture_overlay_open,
            capture::capture_overlay_close,
            capture::capture_overlay_done,
            capture::capture_bar_open,
            capture::capture_bar_close,
            capture::capture_hotkeys_start,
            capture::capture_hotkeys_stop,
            capture::capture_guard
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let resource_dir = strip_verbatim(&handle.path().resource_dir().unwrap_or_default());
            let app_dir = app_dir(&handle);
            let boot_py = boot_py(&handle);
            // Create the data dir up front so the first write (SQLite, config)
            // never races the backend's own mkdir.
            std::fs::create_dir_all(data_dir(&handle)).ok();

            match Server::start(&resource_dir, &boot_py, &app_dir) {
                Ok(srv) => {
                    // The capture commands, granted to this backend's origin
                    // only (capture.rs, pin_capability).
                    if let Err(e) = capture::pin_capability(&handle, srv.port) {
                        eprintln!("{e}");
                    }
                    *shared.lock().unwrap() = Some(srv);
                }
                Err(e) => {
                    // Do not abort: the splash reports this, with the
                    // diagnostics above, which is far more useful than a window
                    // that never appears.
                    eprintln!("failed to start the backend: {e}");
                }
            }
            Ok(())
        })
        .on_window_event(move |window, event| {
            // Stop the server on close rather than waiting for process exit, so
            // the port is free immediately if the user relaunches.
            //
            // The MAIN window's close only: this handler fires for every window
            // the shell owns, and since T3 the screen capture opens and closes
            // windows of its own (the region overlay on each monitor, the
            // recorder bar). Unscoped, the first overlay to close took the
            // backend down with it, mid-recording, with nothing in the log but
            // the request it had just served (measured on the first live run).
            //
            // An overlay closed before it answered - Alt+F4 - is a cancel, as
            // Escape is: the page is told and the other monitors' overlays
            // close (capture.rs, overlay generations).
            if window.label() != "main" {
                if let tauri::WindowEvent::Destroyed = event {
                    capture::overlay_destroyed(window.app_handle(), window.label());
                }
                return;
            }
            // A recording in flight, or its save: the close is refused and the
            // page says why (capture.rs, RECORDING_GUARD) - but only while the
            // backend runs. Once it has exited the guard is ignored: nothing a
            // close could lose is left, and the page that would lower the guard
            // went with it (capture::close_refused).
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let running = for_close
                    .lock()
                    .ok()
                    .map(|mut guard| match guard.as_mut() {
                        Some(srv) => capture::backend_running(true, srv.exited()),
                        None => capture::backend_running(false, None),
                    })
                    .unwrap_or(true);
                if capture::close_refused(capture::recording_in_flight(), running) {
                    api.prevent_close();
                    if let Some(main) = window.app_handle().get_webview_window("main") {
                        let _ = main.unminimize();
                        let _ = main.set_focus();
                    }
                    use tauri::Emitter;
                    let _ = window.emit_to("main", capture::CLOSE_BLOCKED_EVENT, ());
                }
                return;
            }
            if let tauri::WindowEvent::Destroyed = event {
                if let Ok(mut guard) = for_close.lock() {
                    if let Some(srv) = guard.as_mut() {
                        srv.stop();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running the Media Studio Enterprise shell");
}
