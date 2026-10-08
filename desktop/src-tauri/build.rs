// The app's own commands are declared here so Tauri generates an ACL
// permission for each (`allow-<command>` / `deny-<command>`, underscores to
// dashes) - see capabilities/. Without this manifest Tauri lets a LOCAL page
// (the splash on tauri://) call any app command and refuses them all from a
// REMOTE origin; the UI runs on the backend's http origin, which Tauri treats
// as remote, so the screen-capture commands it calls need the manifest and a
// capability that names them (granted at run time to the backend's own port:
// src/capture.rs, pin_capability). Once the manifest exists every app command
// is checked, the splash's included - hence the whole list (capabilities/default.json).
fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            // the splash (desktop/dist/index.html, local origin)
            "server_url",
            "server_log",
            "server_alive",
            "server_ready",
            "env_report",
            "diagnostics",
            "save_report",
            "restart_server",
            "open_state_dir",
            // screen capture (the UI on the backend's http origin)
            "capture_monitors",
            "capture_windows",
            "capture_overlay_open",
            "capture_overlay_close",
            "capture_overlay_done",
            "capture_bar_open",
            "capture_bar_close",
            "capture_hotkeys_start",
            "capture_hotkeys_stop",
            "capture_guard",
        ])),
    )
    .expect("failed to run tauri-build");
}
