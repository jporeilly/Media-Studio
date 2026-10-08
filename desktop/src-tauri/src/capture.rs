//! Screen capture (T3): what the SHELL has to do for it, because the page
//! cannot - the picture itself is taken by the page with `getDisplayMedia`
//! (system sound comes only that way), and the stills by the backend with the
//! bundled ffmpeg's gdigrab. The shell contributes three things:
//!
//! 1. **The monitors and the windows**, in PHYSICAL virtual-screen pixels: the
//!    region overlay draws the window-snap outline from the window list, and
//!    the backend crops stills and recordings at these coordinates. This
//!    process is per-monitor DPI aware (Tauri's manifest), so `GetMonitorInfoW`
//!    and `DwmGetWindowAttribute` answer in physical pixels here; the page's
//!    own `screen.*` values are CSS pixels of ONE monitor and cannot name a
//!    point on another.
//! 2. **The region overlay**: one undecorated, always-on-top window PER
//!    MONITOR, each sized to its monitor in physical pixels. One window over
//!    the whole virtual screen would be rendered at a single DPI and
//!    stretched by the DWM over the other monitors; per monitor, each page's
//!    `devicePixelRatio` is its own monitor's scale and the mapping is exact:
//!    `physical = monitor.x + cssX * devicePixelRatio` (likewise y). The page
//!    (`capture-overlay.html`, served by the backend like the rest of the UI)
//!    shows a frozen screenshot of its monitor and draws the crosshairs, the
//!    size, the magnifier and the snap outline over it.
//! 3. **The hand-back**: the overlay reports its choice through a command
//!    (`capture_overlay_done`), which emits `capture:region` to the main
//!    window and closes every overlay. A command rather than a page-to-page
//!    event so the overlay needs no event permission of its own.
//!
//! The UI runs on the backend's http origin, which Tauri treats as REMOTE:
//! every command here must be granted to that origin - at run time, to the
//! backend's own port only (`pin_capability`) - and `build.rs` declares them
//! so the ACL knows them.
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindowBuilder};

/// Whether a recording is in flight (the page says so through `capture_guard`).
/// While it is, closing the main window is refused (`main.rs`): the webview's
/// own `beforeunload` is never consulted when the HOST window closes, and the
/// window is minimised while recording, so the taskbar's Close is the easy
/// accident - it would kill the backend with the job object and lose the
/// recording's last seconds, or its save.
static RECORDING_GUARD: AtomicBool = AtomicBool::new(false);
pub const CLOSE_BLOCKED_EVENT: &str = "capture:close-blocked";

pub fn recording_in_flight() -> bool {
    RECORDING_GUARD.load(Ordering::SeqCst)
}

/// The page tells the shell when a recording starts (true) and ends (false).
#[tauri::command]
pub fn capture_guard(active: bool) {
    RECORDING_GUARD.store(active, Ordering::SeqCst);
}

/// Whether the backend is still running: started, and not known to have
/// exited (`Server::exited` answers `Some(true)` once it has; `None` when the
/// question could not be asked, which is not proof of an exit).
pub fn backend_running(started: bool, exited: Option<bool>) -> bool {
    started && exited != Some(true)
}

/// Whether a close of the main window is refused: the page's guard is up (a
/// recording in flight, or its save running) AND the backend still runs. Once
/// the backend has exited - or never started - there is nothing a close could
/// lose, and a guard the page could not lower (it went with the backend) must
/// never leave a window that cannot be closed. Pure, so `cargo test` pins it.
pub fn close_refused(guard_up: bool, backend_running: bool) -> bool {
    guard_up && backend_running
}

/// The commands the UI calls for screen capture: granted to the backend's own
/// origin only, by `pin_capability`. Of Tauri's core, only what the pages use:
/// events (the bar and the main window talk through them) and four window
/// calls (minimise the main window while recording, bring it back, drag the
/// bar) - not `core:default`, which would also let the http origin make
/// menus and tray icons and read every window's state.
pub const CAPTURE_PERMISSIONS: &[&str] = &[
    "core:event:default",
    "core:window:allow-minimize",
    "core:window:allow-unminimize",
    "core:window:allow-set-focus",
    "core:window:allow-start-dragging",
    "allow-capture-monitors",
    "allow-capture-windows",
    "allow-capture-overlay-open",
    "allow-capture-overlay-close",
    "allow-capture-overlay-done",
    "allow-capture-bar-open",
    "allow-capture-bar-close",
    "allow-capture-hotkeys-start",
    "allow-capture-hotkeys-stop",
    "allow-capture-guard",
];

/// Grant the capture commands to the UI - and to nothing else on this machine.
///
/// The UI is served by the backend over http, which Tauri treats as a REMOTE
/// origin, so the commands must be granted to it explicitly. A capability file
/// can only name a pattern fixed at build time, and `http://127.0.0.1:*` would
/// grant them to ANY local web app a window of ours ever showed. So the grant
/// is made here at run time for the one port the shell started the backend on
/// (`127.0.0.1` and `localhost`, every path), in the main window, the overlay
/// windows and the recorder bar. Called once per port: after an in-place
/// restart on a new port the new one is added (Tauri cannot withdraw a
/// capability, so the old port's grant stays until the app closes).
pub fn pin_capability(app: &AppHandle, port: u16) -> Result<(), String> {
    use tauri::ipc::CapabilityBuilder;

    // The overlay and the bar are opened on THIS backend's origin; no page
    // names the URL they load.
    *BACKEND_ORIGIN.lock().map_err(|_| "capture origin state poisoned".to_string())? = Some(backend_origin(port));
    static PINNED: Mutex<Vec<u16>> = Mutex::new(Vec::new());
    let mut pinned = PINNED.lock().map_err(|_| "capture pin state poisoned".to_string())?;
    if pinned.contains(&port) {
        return Ok(());
    }
    // Not the local (tauri://) pages either: the only one is the splash, which
    // needs no capture command.
    let mut capability = CapabilityBuilder::new(format!("capture-{port}"))
        .local(false)
        .windows(capture_windows_labels());
    for origin in capture_origins(port) {
        capability = capability.remote(origin);
    }
    for permission in CAPTURE_PERMISSIONS {
        capability = capability.permission(*permission);
    }
    app.add_capability(capability).map_err(|e| format!("capture capability for port {port}: {e}"))?;
    pinned.push(port);
    Ok(())
}

/// The origins the capture commands are granted to: the backend's port on
/// both names of this computer, and nothing else - no other port, no
/// wildcard, no other host. Pure, so `cargo test` pins it.
pub fn capture_origins(port: u16) -> [String; 2] {
    [format!("http://127.0.0.1:{port}"), format!("http://localhost:{port}")]
}

/// The windows the grant applies to: the UI, the overlays, the bar.
pub fn capture_windows_labels() -> [&'static str; 3] {
    ["main", "capture-overlay-*", BAR_LABEL]
}

/// The origin the shell loaded the UI from (`Server::url`): the overlay and
/// the bar are opened there and nowhere else.
pub fn backend_origin(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

/// The backend's origin, set by `pin_capability` on every (re)start.
static BACKEND_ORIGIN: Mutex<Option<String>> = Mutex::new(None);

fn current_origin() -> Result<String, String> {
    BACKEND_ORIGIN
        .lock()
        .ok()
        .and_then(|o| o.clone())
        .ok_or_else(|| "The backend has not started, so there is no page to open.".to_string())
}

/// The overlay page for one monitor of one generation, on `origin`.
pub fn overlay_url(origin: &str, monitor: usize, generation: u64) -> String {
    format!("{origin}/capture-overlay.html?monitor={monitor}&gen={generation}")
}

/// The recorder bar's page, on `origin`.
pub fn bar_url(origin: &str) -> String {
    format!("{origin}/capture-bar.html")
}

/// Overlay generations: each `capture_overlay_open` is a new one, and its
/// windows are labelled `capture-overlay-<generation>-<monitor>`. A
/// generation is ANSWERED once the page chose (`capture_overlay_done`) or the
/// UI closed it (`capture_overlay_close`). A window of the current
/// generation destroyed before that - Alt+F4 - is a cancel: the main window
/// is told `null`, as for Escape, and the other monitors' overlays close.
static OVERLAY_GEN: AtomicU64 = AtomicU64::new(0);
static ANSWERED_GEN: AtomicU64 = AtomicU64::new(0);

/// The generation an overlay label belongs to, or None for another window.
pub fn overlay_generation(label: &str) -> Option<u64> {
    let rest = label.strip_prefix(OVERLAY_PREFIX)?;
    let (generation, monitor) = rest.split_once('-')?;
    monitor.parse::<usize>().ok()?;
    generation.parse().ok()
}

/// Whether the window `label`, just destroyed, leaves the current overlay
/// unanswered: an overlay of the CURRENT generation that nobody answered.
/// A window of an older generation, or any other window, is not. Pure.
pub fn closed_unanswered(label: &str, current: u64, answered: u64) -> bool {
    matches!(overlay_generation(label), Some(g) if g == current && answered < current)
}

/// Mark `generation` answered; true when this call was the first to.
fn answer(generation: u64) -> bool {
    ANSWERED_GEN.fetch_max(generation, Ordering::SeqCst) < generation
}

/// The window event hook for overlay windows (`main.rs`): an overlay closed
/// before it answered - Alt+F4, or the window killed - is a cancel.
pub fn overlay_destroyed(app: &AppHandle, label: &str) {
    let current = OVERLAY_GEN.load(Ordering::SeqCst);
    if closed_unanswered(label, current, ANSWERED_GEN.load(Ordering::SeqCst)) && answer(current) {
        let _ = app.emit_to("main", REGION_EVENT, Option::<Region>::None);
        close_overlays(app);
    }
}

fn close_overlays(app: &AppHandle) {
    for (label, window) in app.webview_windows() {
        if label.starts_with(OVERLAY_PREFIX) {
            let _ = window.close();
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct MonitorInfo {
    pub index: usize,
    pub name: String,
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    /// Windows' scale factor for this monitor (DPI / 96): what the overlay
    /// page sees as `devicePixelRatio`.
    pub scale: f64,
    pub primary: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct WindowInfo {
    pub hwnd: isize,
    pub title: String,
    /// The visible frame (`DWMWA_EXTENDED_FRAME_BOUNDS`): what a user sees as
    /// the window, without the invisible resize borders `GetWindowRect`
    /// includes (7 px a side on Windows 10/11).
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

/// What the overlay chose: a region in physical virtual-screen pixels, and
/// the window it snapped to when it did (its title names the recording).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Region {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub monitor: usize,
    #[serde(default)]
    pub window_title: Option<String>,
    #[serde(default)]
    pub hwnd: Option<isize>,
}

pub const OVERLAY_PREFIX: &str = "capture-overlay-";
pub const REGION_EVENT: &str = "capture:region";
/// The floating recorder bar's window label, and the event the global
/// hotkeys raise in the main window (payload `"pause"` or `"stop"`).
pub const BAR_LABEL: &str = "capture-bar";
pub const HOTKEY_EVENT: &str = "capture:hotkey";

#[cfg(windows)]
mod win {
    use super::{MonitorInfo, WindowInfo};
    use windows::Win32::Foundation::{BOOL, HWND, LPARAM, RECT};
    use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS};
    use windows::Win32::Graphics::Gdi::{EnumDisplayMonitors, GetMonitorInfoW, HDC, HMONITOR, MONITORINFO, MONITORINFOEXW};

    /// `MONITORINFO.dwFlags` bit for the primary monitor (the `windows`
    /// crate's Gdi module does not export the `MONITORINFOF_PRIMARY` name).
    const MONITORINFOF_PRIMARY: u32 = 0x0000_0001;
    use windows::Win32::System::Threading::GetCurrentProcessId;
    use windows::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowLongPtrW, GetWindowTextW, GetWindowThreadProcessId, IsIconic, IsWindowVisible,
        GWL_EXSTYLE, WS_EX_TOOLWINDOW,
    };

    pub fn monitors() -> Vec<MonitorInfo> {
        unsafe extern "system" fn cb(hmon: HMONITOR, _hdc: HDC, _rc: *mut RECT, lparam: LPARAM) -> BOOL {
            let out = &mut *(lparam.0 as *mut Vec<MonitorInfo>);
            let mut info = MONITORINFOEXW::default();
            info.monitorInfo.cbSize = std::mem::size_of::<MONITORINFOEXW>() as u32;
            if GetMonitorInfoW(hmon, &mut info.monitorInfo as *mut MONITORINFO).as_bool() {
                let (mut dx, mut dy) = (96u32, 96u32);
                let _ = GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI, &mut dx, &mut dy);
                let r = info.monitorInfo.rcMonitor;
                let name_len = info.szDevice.iter().position(|&c| c == 0).unwrap_or(info.szDevice.len());
                out.push(MonitorInfo {
                    index: out.len(),
                    name: String::from_utf16_lossy(&info.szDevice[..name_len]),
                    x: r.left,
                    y: r.top,
                    width: (r.right - r.left).max(0) as u32,
                    height: (r.bottom - r.top).max(0) as u32,
                    scale: dx as f64 / 96.0,
                    primary: (info.monitorInfo.dwFlags & MONITORINFOF_PRIMARY) != 0,
                });
            }
            BOOL(1)
        }
        let mut out: Vec<MonitorInfo> = Vec::new();
        unsafe {
            let _ = EnumDisplayMonitors(None, None, Some(cb), LPARAM(&mut out as *mut _ as isize));
        }
        // The primary first, then by position: a stable order the overlay
        // labels and the region record can refer to.
        out.sort_by_key(|m| (!m.primary, m.x, m.y));
        for (i, m) in out.iter_mut().enumerate() {
            m.index = i;
        }
        out
    }

    /// The windows a user would point at, top-most first (`EnumWindows`
    /// walks the z-order): visible, not minimised, not cloaked (a UWP app on
    /// another virtual desktop stays "visible" but cloaked), not a tool
    /// window, with a title, and not one of ours.
    pub fn windows() -> Vec<WindowInfo> {
        unsafe extern "system" fn cb(hwnd: HWND, lparam: LPARAM) -> BOOL {
            let out = &mut *(lparam.0 as *mut Vec<WindowInfo>);
            if !IsWindowVisible(hwnd).as_bool() || IsIconic(hwnd).as_bool() {
                return BOOL(1);
            }
            let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE) as u32;
            if ex & WS_EX_TOOLWINDOW.0 != 0 {
                return BOOL(1);
            }
            let mut cloaked: u32 = 0;
            if DwmGetWindowAttribute(
                hwnd,
                DWMWA_CLOAKED,
                &mut cloaked as *mut _ as *mut core::ffi::c_void,
                std::mem::size_of::<u32>() as u32,
            )
            .is_ok()
                && cloaked != 0
            {
                return BOOL(1);
            }
            let mut pid = 0u32;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            if pid == GetCurrentProcessId() {
                return BOOL(1);
            }
            let mut title = [0u16; 512];
            let n = GetWindowTextW(hwnd, &mut title);
            if n <= 0 {
                return BOOL(1);
            }
            let mut rect = RECT::default();
            if DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                &mut rect as *mut _ as *mut core::ffi::c_void,
                std::mem::size_of::<RECT>() as u32,
            )
            .is_err()
            {
                return BOOL(1);
            }
            let (w, h) = (rect.right - rect.left, rect.bottom - rect.top);
            if w <= 0 || h <= 0 {
                return BOOL(1);
            }
            out.push(WindowInfo {
                hwnd: hwnd.0 as isize,
                title: String::from_utf16_lossy(&title[..n as usize]),
                x: rect.left,
                y: rect.top,
                width: w as u32,
                height: h as u32,
            });
            BOOL(1)
        }
        let mut out: Vec<WindowInfo> = Vec::new();
        unsafe {
            let _ = EnumWindows(Some(cb), LPARAM(&mut out as *mut _ as isize));
        }
        out
    }
}

#[cfg(windows)]
mod affinity {
    //! Keep the recorder bar out of every recording: `WDA_EXCLUDEFROMCAPTURE`
    //! (Windows 10 2004+) makes a window invisible to screen capture - the
    //! Graphics Capture and desktop-duplication paths Chromium records with,
    //! and GDI's BitBlt alike - while it stays on the monitor. A failure (an
    //! older Windows) is not fatal: the bar is then simply in the picture.
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{SetWindowDisplayAffinity, WDA_EXCLUDEFROMCAPTURE};

    pub fn exclude(hwnd: *mut core::ffi::c_void) -> bool {
        unsafe { SetWindowDisplayAffinity(HWND(hwnd), WDA_EXCLUDEFROMCAPTURE).is_ok() }
    }
}

#[cfg(windows)]
mod hotkeys {
    //! Snagit's recording keys, system-wide: Shift+F9 pause/resume, Shift+F10
    //! stop. `RegisterHotKey` binds a key to the THREAD that registers it and
    //! delivers `WM_HOTKEY` to that thread's message queue, so a thread of our
    //! own runs the queue while a recording lasts and raises the event in the
    //! main window; `stop` posts it `WM_QUIT`. The keys are taken only while
    //! recording, so the rest of the time they stay with whatever else wants
    //! them (ShareX, Snagit itself).
    use std::sync::{mpsc, Mutex};

    use tauri::{AppHandle, Emitter};
    use windows::Win32::Foundation::{LPARAM, WPARAM};
    use windows::Win32::System::Threading::GetCurrentThreadId;
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        RegisterHotKey, UnregisterHotKey, MOD_NOREPEAT, MOD_SHIFT, VK_F10, VK_F9,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetMessageW, PostThreadMessageW, MSG, WM_HOTKEY, WM_QUIT};

    const ID_PAUSE: i32 = 1;
    const ID_STOP: i32 = 2;
    static THREAD: Mutex<Option<u32>> = Mutex::new(None);

    pub fn start(app: AppHandle) -> Result<(), String> {
        let mut guard = THREAD.lock().map_err(|_| "hotkey state poisoned".to_string())?;
        if guard.is_some() {
            return Ok(());
        }
        let (tx, rx) = mpsc::channel::<Result<u32, String>>();
        std::thread::spawn(move || unsafe {
            let tid = GetCurrentThreadId();
            let pause = RegisterHotKey(None, ID_PAUSE, MOD_SHIFT | MOD_NOREPEAT, VK_F9.0 as u32);
            let stop = RegisterHotKey(None, ID_STOP, MOD_SHIFT | MOD_NOREPEAT, VK_F10.0 as u32);
            if pause.is_err() || stop.is_err() {
                let _ = UnregisterHotKey(None, ID_PAUSE);
                let _ = UnregisterHotKey(None, ID_STOP);
                let _ = tx.send(Err(
                    "Shift+F9 / Shift+F10 are taken by another program, so the recording keys are off.".into(),
                ));
                return;
            }
            let _ = tx.send(Ok(tid));
            let mut msg = MSG::default();
            while GetMessageW(&mut msg, None, 0, 0).as_bool() {
                if msg.message == WM_HOTKEY {
                    let action = match msg.wParam.0 as i32 {
                        ID_PAUSE => "pause",
                        ID_STOP => "stop",
                        _ => continue,
                    };
                    let _ = app.emit_to("main", super::HOTKEY_EVENT, action);
                }
            }
            let _ = UnregisterHotKey(None, ID_PAUSE);
            let _ = UnregisterHotKey(None, ID_STOP);
        });
        match rx.recv() {
            Ok(Ok(tid)) => {
                *guard = Some(tid);
                Ok(())
            }
            Ok(Err(e)) => Err(e),
            Err(_) => Err("The hotkey thread did not start.".into()),
        }
    }

    pub fn stop() {
        if let Ok(mut guard) = THREAD.lock() {
            if let Some(tid) = guard.take() {
                unsafe {
                    let _ = PostThreadMessageW(tid, WM_QUIT, WPARAM(0), LPARAM(0));
                }
            }
        }
    }
}

#[cfg(not(windows))]
mod win {
    use super::{MonitorInfo, WindowInfo};
    pub fn monitors() -> Vec<MonitorInfo> {
        Vec::new()
    }
    pub fn windows() -> Vec<WindowInfo> {
        Vec::new()
    }
}

#[cfg(not(windows))]
mod affinity {
    pub fn exclude(_hwnd: *mut core::ffi::c_void) -> bool {
        false
    }
}

#[cfg(not(windows))]
mod hotkeys {
    use tauri::AppHandle;
    pub fn start(_app: AppHandle) -> Result<(), String> {
        Err("Recording hotkeys are available on Windows only.".into())
    }
    pub fn stop() {}
}

/// The monitors, physical virtual-screen pixels, primary first.
#[tauri::command]
pub fn capture_monitors() -> Vec<MonitorInfo> {
    win::monitors()
}

/// The windows a user could snap to, top-most first.
#[tauri::command]
pub fn capture_windows() -> Vec<WindowInfo> {
    win::windows()
}

/// Open the region overlay: one window per monitor, each covering its
/// monitor exactly, at the backend's `capture-overlay.html` with
/// `?monitor=<index>&gen=<generation>` - built HERE from the origin the shell
/// started the backend on (`pin_capability`), never taken from the page, so
/// no page can open an always-on-top, full-monitor, undecorated window on
/// another site. Same origin as the UI, so its session cookie and its API are
/// at hand; only the primary monitor's window takes the focus. Each call is
/// a new generation (`OVERLAY_GEN`): its windows are labelled with it, and
/// one closed before it answered (Alt+F4) cancels the pick.
///
/// `async`, like `server_ready`: a plain command runs ON the main thread,
/// and on Windows `WebviewWindowBuilder::build` waits for the main thread
/// to create the webview - called from it, that is a deadlock that froze
/// the whole shell with one half-made `about:blank` window (measured on
/// the first run). On a worker thread the build dispatches to the loop and
/// returns.
///
/// `only` restricts the overlay to the monitors with these indexes - for a
/// test harness on a shared desktop; the UI never passes it, so every
/// monitor is covered.
#[tauri::command(async)]
pub fn capture_overlay_open(app: AppHandle, only: Option<Vec<usize>>) -> Result<Vec<MonitorInfo>, String> {
    let origin = current_origin()?;
    let monitors: Vec<MonitorInfo> = win::monitors()
        .into_iter()
        .filter(|m| only.as_ref().map_or(true, |keep| keep.contains(&m.index)))
        .collect();
    if monitors.is_empty() {
        return Err("No monitor was found.".into());
    }
    capture_overlay_close(app.clone());
    let generation = OVERLAY_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    for m in &monitors {
        let url: tauri::Url = overlay_url(&origin, m.index, generation)
            .parse()
            .map_err(|e| format!("Bad overlay URL: {e}"))?;
        let label = format!("{OVERLAY_PREFIX}{generation}-{}", m.index);
        let window = WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(url))
            .title("Capture")
            .decorations(false)
            // No shadow: an undecorated window otherwise keeps an invisible
            // frame (8 px a side, 1 px on top) that set_position places by
            // its outer edge, and the page sat 8 px right of the monitor's
            // edge with a strip of desktop showing (measured: the page's
            // screenX was 8 px right of its monitor's left edge).
            .shadow(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .resizable(false)
            .maximizable(false)
            .minimizable(false)
            .visible(false)
            .focused(m.primary)
            .build()
            .map_err(|e| format!("Could not open the overlay: {e}"))?;
        // Physical, after the build: the builder's position and size are
        // logical and would be scaled by whichever monitor it lands on.
        window
            .set_position(PhysicalPosition::new(m.x, m.y))
            .and_then(|_| window.set_size(PhysicalSize::new(m.width, m.height)))
            .and_then(|_| window.show())
            .map_err(|e| format!("Could not place the overlay: {e}"))?;
    }
    Ok(monitors)
}

/// Close every overlay window (the UI's cancel, or after
/// `capture_overlay_done`): the current generation is answered, so its
/// windows closing is no Alt+F4. `async` for the same reason as
/// `capture_overlay_open`.
#[tauri::command(async)]
pub fn capture_overlay_close(app: AppHandle) {
    answer(OVERLAY_GEN.load(Ordering::SeqCst));
    close_overlays(&app);
}

/// The overlay's answer: `Some(region)` when the user chose one, `None` on
/// Escape. Emitted to the main window as `capture:region` (its payload is
/// the region or null), then the overlays close. `async` as the others.
#[tauri::command(async)]
pub fn capture_overlay_done(app: AppHandle, region: Option<Region>) -> Result<(), String> {
    answer(OVERLAY_GEN.load(Ordering::SeqCst));
    app.emit_to("main", REGION_EVENT, region)
        .map_err(|e| format!("Could not hand the region to the main window: {e}"))?;
    capture_overlay_close(app);
    Ok(())
}

/// Open the floating recorder bar: a small undecorated always-on-top window
/// at the backend's `capture-bar.html` (built here from the backend's
/// origin, like the overlay's), placed at physical `x`,`y`
/// with the given physical size, and excluded from screen capture so it
/// never appears in a recording wherever it sits (`affinity`). Returns
/// whether the exclusion took: false on a Windows older than 10 2004, where
/// the bar is in the picture and the UI should say so.
#[tauri::command(async)]
pub fn capture_bar_open(app: AppHandle, x: i32, y: i32, width: u32, height: u32) -> Result<bool, String> {
    let url: tauri::Url = bar_url(&current_origin()?).parse().map_err(|e| format!("Bad bar URL: {e}"))?;
    capture_bar_close(app.clone());
    let window = WebviewWindowBuilder::new(&app, BAR_LABEL, WebviewUrl::External(url))
        .title("Recording")
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .maximizable(false)
        .minimizable(false)
        .visible(false)
        .focused(false)
        .build()
        .map_err(|e| format!("Could not open the recorder bar: {e}"))?;
    window
        .set_position(PhysicalPosition::new(x, y))
        .and_then(|_| window.set_size(PhysicalSize::new(width, height)))
        .map_err(|e| format!("Could not place the recorder bar: {e}"))?;
    #[cfg(windows)]
    let excluded = window
        .hwnd()
        .map(|h| affinity::exclude(h.0 as *mut core::ffi::c_void))
        .unwrap_or(false);
    #[cfg(not(windows))]
    let excluded = false;
    window.show().map_err(|e| format!("Could not show the recorder bar: {e}"))?;
    Ok(excluded)
}

/// Close the recorder bar if it is open.
#[tauri::command(async)]
pub fn capture_bar_close(app: AppHandle) {
    if let Some(window) = app.get_webview_window(BAR_LABEL) {
        let _ = window.close();
    }
}

/// Take the recording keys (Shift+F9 pause/resume, Shift+F10 stop) for the
/// length of a recording; `capture:hotkey` is raised in the main window with
/// `"pause"` or `"stop"`. Err when another program holds them.
#[tauri::command(async)]
pub fn capture_hotkeys_start(app: AppHandle) -> Result<(), String> {
    hotkeys::start(app)
}

/// Give the recording keys back.
#[tauri::command(async)]
pub fn capture_hotkeys_stop() {
    hotkeys::stop();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_grant_is_the_backends_port_on_this_computer_only() {
        assert_eq!(capture_origins(5680), ["http://127.0.0.1:5680".to_string(), "http://localhost:5680".to_string()]);
        for origin in capture_origins(58866) {
            assert!(origin.ends_with(":58866"), "{origin}");
            assert!(!origin.contains('*'), "a wildcard grants every local web app: {origin}");
            let host = origin.trim_start_matches("http://").split(':').next().unwrap();
            assert!(host == "127.0.0.1" || host == "localhost", "{origin}");
        }
        // The UI is loaded from the first (Server::url): the pages it opens are on the granted origin.
        assert_eq!(backend_origin(58866), capture_origins(58866)[0]);
        assert_eq!(capture_windows_labels(), ["main", "capture-overlay-*", BAR_LABEL]);
    }

    #[test]
    fn the_grant_names_only_the_core_calls_the_pages_use() {
        let core: Vec<&str> = CAPTURE_PERMISSIONS.iter().copied().filter(|p| p.starts_with("core:")).collect();
        assert_eq!(core, [
            "core:event:default",
            "core:window:allow-minimize",
            "core:window:allow-unminimize",
            "core:window:allow-set-focus",
            "core:window:allow-start-dragging",
        ]);
        for p in CAPTURE_PERMISSIONS.iter().filter(|p| !p.starts_with("core:")) {
            assert!(p.starts_with("allow-capture-"), "{p}");
        }
        assert_eq!(CAPTURE_PERMISSIONS.len(), 5 + 10);
    }

    #[test]
    fn the_overlay_and_the_bar_open_on_the_backends_origin() {
        let origin = backend_origin(51234);
        assert_eq!(overlay_url(&origin, 2, 7), "http://127.0.0.1:51234/capture-overlay.html?monitor=2&gen=7");
        assert_eq!(bar_url(&origin), "http://127.0.0.1:51234/capture-bar.html");
        let parsed: tauri::Url = overlay_url(&origin, 0, 1).parse().unwrap();
        assert_eq!((parsed.host_str(), parsed.port()), (Some("127.0.0.1"), Some(51234)));
    }

    #[test]
    fn the_close_guard_holds_only_while_the_backend_runs() {
        // The backend running, the guard up: refused.
        assert!(close_refused(true, backend_running(true, Some(false))));
        // Running but unasked (try_wait failed): still taken as running.
        assert!(close_refused(true, backend_running(true, None)));
        // The backend has exited, or never started: the guard is ignored.
        assert!(!close_refused(true, backend_running(true, Some(true))));
        assert!(!close_refused(true, backend_running(false, None)));
        // No guard: never refused.
        assert!(!close_refused(false, backend_running(true, Some(false))));
    }

    #[test]
    fn an_overlay_closed_before_it_answered_is_a_cancel() {
        assert_eq!(overlay_generation("capture-overlay-3-0"), Some(3));
        assert_eq!(overlay_generation("capture-overlay-12-4"), Some(12));
        assert_eq!(overlay_generation("capture-overlay-0"), None);
        assert_eq!(overlay_generation("capture-bar"), None);
        assert_eq!(overlay_generation("main"), None);
        // Alt+F4 on the current, unanswered overlay: a cancel.
        assert!(closed_unanswered("capture-overlay-3-1", 3, 2));
        // Closed by its own answer (done) or by the UI (close): not.
        assert!(!closed_unanswered("capture-overlay-3-1", 3, 3));
        // An older generation's window closing late: not.
        assert!(!closed_unanswered("capture-overlay-2-0", 3, 2));
        // Other windows: never.
        assert!(!closed_unanswered("capture-bar", 3, 2));
        assert!(!closed_unanswered("main", 3, 2));
    }
}
