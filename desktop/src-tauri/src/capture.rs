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

/// The recorder bar's page, on `origin`; in its second mode (the guided
/// capture of a walkthrough's steps, W2) the page is asked for `?mode=steps`.
pub fn bar_url(origin: &str, mode: BarMode) -> String {
    match mode {
        BarMode::Recording => format!("{origin}/capture-bar.html"),
        BarMode::Steps => format!("{origin}/capture-bar.html?mode=steps"),
    }
}

/// The capture bar's two modes: the recorder's (pause / stop, the clock) and
/// the guided capture's (the walkthrough's step, Capture / Skip / Back /
/// Done). The same window and page; the page lays itself out by the mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BarMode {
    Recording,
    Steps,
}

impl BarMode {
    /// The page's `mode` argument: `None` or `"recording"` is the recorder's
    /// bar, `"steps"` the guided capture's; anything else is refused.
    pub fn parse(mode: Option<&str>) -> Result<Self, String> {
        match mode {
            None | Some("") | Some("recording") => Ok(BarMode::Recording),
            Some("steps") => Ok(BarMode::Steps),
            Some(other) => Err(format!("Unknown capture bar mode '{other}'.")),
        }
    }

    pub fn title(self) -> &'static str {
        match self {
            BarMode::Recording => "Recording",
            BarMode::Steps => "Capture steps",
        }
    }

    /// What the bar is called in the shell's own refusals ("Could not open
    /// the capture bar: …"): the recorder bar in the recorder's mode, the
    /// capture bar in the guided capture's (W2b, D2).
    pub fn bar_name(self) -> &'static str {
        match self {
            BarMode::Recording => "recorder bar",
            BarMode::Steps => "capture bar",
        }
    }
}

/// The two sets of global keys the shell takes for the page: the recorder's
/// (Shift+F9 pause / resume, Shift+F10 stop) and the guided capture's
/// (Shift+F8 capture, raised WITH the pointer's position - and, when the page
/// asks for it, the click hook: every click is a step, W2b). One set at a
/// time: the keys are taken only while the recording or the capture runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeySet {
    Recording,
    Steps,
}

/// Which key set installs the click hook: the guided capture's, and only
/// when the page asks for it (`clicks`); the recorder's never. Pure.
pub fn hook_wanted(set: KeySet, clicks: bool) -> bool {
    set == KeySet::Steps && clicks
}

/// What `capture_hotkeys_start` answers (H1): whether the set's key went up
/// (`key`) and whether the click hook did (`clicks`). The page says which of
/// the two is off; the recorder's caller reads only the refusal.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct KeysUp {
    pub key: bool,
    pub clicks: bool,
}

/// The thread's decision once it has tried its keys (`key_ok`) and, when
/// asked, the click hook (`hook_ok`; the hook is tried whether or not the
/// key went up): the answer, and whether the thread runs on - while anything
/// is up. The recorder's set is its keys or nothing: taken keys refuse it
/// (Err, as always). The guided capture's set answers both facts whatever
/// they are, so a Shift+F8 held by another program never switches the clicks
/// off - clicks are the main way to capture. Pure.
pub fn keys_outcome(set: KeySet, key_ok: bool, hook_ok: bool) -> Result<(KeysUp, bool), ()> {
    match set {
        KeySet::Recording if !key_ok => Err(()),
        _ => Ok((KeysUp { key: key_ok, clicks: hook_ok }, key_ok || hook_ok)),
    }
}

/// The second press of a double click is not a step (the first press already
/// took the still): a press of the SAME button within `DOUBLE_CLICK_MS` of the
/// previous EMITTED one, at a point within `DOUBLE_CLICK_PX` of it on both
/// axes. A right press on the heels of a left one IS a step ("click the step,
/// then right-click it" is the commonest PDI gesture).
pub const DOUBLE_CLICK_MS: u64 = 400;
pub const DOUBLE_CLICK_PX: i32 = 8;

/// Whether `point`, pressed with `button` at `now_ms`, repeats the previous
/// emitted press (`previous`: its point, its button and its time on the same
/// clock). Pure.
pub fn repeats_press(previous: Option<(Pointer, ClickButton, u64)>, point: Pointer, button: ClickButton, now_ms: u64) -> bool {
    match previous {
        Some((p, b, at)) => {
            b == button
                && now_ms.saturating_sub(at) <= DOUBLE_CLICK_MS
                && (point.x - p.x).abs() <= DOUBLE_CLICK_PX
                && (point.y - p.y).abs() <= DOUBLE_CLICK_PX
        }
        None => false,
    }
}

/// A top-level window's VISIBLE frame in physical virtual-screen pixels (the
/// DWM's extended frame bounds, else `GetWindowRect`): the own-windows list
/// the hook thread keeps.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WindowRect {
    pub left: i32,
    pub top: i32,
    pub right: i32,
    pub bottom: i32,
}

/// The own-window filter: a press on one of THIS process's top-level windows
/// - the capture bar, an overlay, the app window - is not a step. `rects` are
/// their visible frames, read from our own handles (never a hit-test: the
/// window under a point in a Tauri window is WebView2's, owned by another
/// process); half-open on both axes. Pure.
pub fn point_in_any(rects: &[WindowRect], point: Pointer) -> bool {
    rects
        .iter()
        .any(|r| point.x >= r.left && point.x < r.right && point.y >= r.top && point.y < r.bottom)
}

/// What says whether one of our top-level windows can take a press: shown
/// (`IsWindowVisible`), not cloaked (`DWMWA_CLOAKED`: a window on another
/// virtual desktop, or one an app hid that way), not click-through
/// (`WS_EX_TRANSPARENT` - tao's own event-target window is a visible,
/// layered, click-through 16 x 16 at the primary monitor's origin, and a
/// press there lands on the window beneath), not minimised (`IsIconic`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct WindowFlags {
    pub visible: bool,
    pub cloaked: bool,
    pub click_through: bool,
    pub minimised: bool,
}

/// Whether a window with these flags can take a press, so that a press over
/// it is ours and not a step: visible, not cloaked, not click-through, not
/// minimised. Pure.
pub fn can_take_press(flags: WindowFlags) -> bool {
    flags.visible && !flags.cloaked && !flags.click_through && !flags.minimised
}

/// The hook's hand-off to its thread's loop: a left or a right press, posted
/// as one of these two messages (`WM_APP` + 0x21 / 0x22; the thread owns no
/// window and nothing else posts to its queue), the point in the message's
/// two words (`pack_point`).
pub const WM_STEP_LEFT: u32 = 0x8000 + 0x21;
pub const WM_STEP_RIGHT: u32 = 0x8000 + 0x22;
/// The hook messages that are a press: `WM_LBUTTONDOWN` and `WM_RBUTTONDOWN`
/// (the values the `windows` crate names; a test pins them to it).
const HOOK_LEFT_DOWN: u32 = 0x0201;
const HOOK_RIGHT_DOWN: u32 = 0x0204;

/// The callback's choice: which message a hook message is posted as - a left
/// or right press, and nothing else (mouse-up, the wheel, a move, the other
/// buttons are never posted). Pure.
pub fn step_message(hook_message: u32) -> Option<u32> {
    match hook_message {
        HOOK_LEFT_DOWN => Some(WM_STEP_LEFT),
        HOOK_RIGHT_DOWN => Some(WM_STEP_RIGHT),
        _ => None,
    }
}

/// The point packed into a posted message's two words: x in wParam (through
/// u32, so zero-extended into usize on either width), y in lParam
/// (sign-extended into isize). Whole i32s either way, negative ones included
/// (a monitor left of or above the primary). Pure.
pub fn pack_point(point: Pointer) -> (usize, isize) {
    (point.x as u32 as usize, point.y as isize)
}

/// `pack_point` undone, from the message the loop received. Pure.
pub fn unpack_point(wparam: usize, lparam: isize) -> Pointer {
    Pointer { x: wparam as u32 as i32, y: lparam as i32 }
}

impl KeySet {
    /// The page's `keys` argument: `None` or `"recording"` is the recorder's
    /// set, `"steps"` the guided capture's; anything else is refused.
    pub fn parse(keys: Option<&str>) -> Result<Self, String> {
        match keys {
            None | Some("") | Some("recording") => Ok(KeySet::Recording),
            Some("steps") => Ok(KeySet::Steps),
            Some(other) => Err(format!("Unknown key set '{other}'.")),
        }
    }
}

/// The pointer at the moment the capture key was pressed, in PHYSICAL
/// virtual-screen pixels - the space the overlay's regions, the monitors and
/// the windows are reported in (this process is per-monitor DPI aware, so
/// `GetCursorPos` answers physical pixels). The payload of `capture:step-key`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Pointer {
    pub x: i32,
    pub y: i32,
}

/// Which mouse button a click was (W2b): the payload says `"left"` or
/// `"right"` - a right click is a step like a left one (the context menu is
/// the result on the next still).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ClickButton {
    Left,
    Right,
}

/// A click the hook saw (W2b), in the same PHYSICAL virtual-screen pixels as
/// `Pointer`: the payload of `capture:step-click`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct StepClick {
    pub x: i32,
    pub y: i32,
    pub button: ClickButton,
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

/// The main window going (its `Destroyed` event, `main.rs`): whatever the
/// screen capture still has open goes with it - the region overlays, the bar
/// in either mode - and the global keys are given back, so a guided capture's
/// Shift+F8 (or a recording's Shift+F9 / Shift+F10) is never left registered
/// to a process on its way out, and no orphaned always-on-top bar keeps the
/// process alive once the main window is gone. Each part is idempotent: the
/// page usually released them first (Done, Stop), and nothing held is nothing
/// done. Called before the backend is stopped, from the one line `main.rs`
/// adds to that branch.
pub fn shutdown(app: &AppHandle) {
    close_overlays(app);
    capture_bar_close(app.clone());
    hotkeys::stop();
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
/// The event the guided capture's key (Shift+F8) raises in the main window,
/// with the pointer's position as its payload (`Pointer`).
pub const STEP_KEY_EVENT: &str = "capture:step-key";
/// The event the click hook raises in the main window for a left or right
/// press outside this process's windows (W2b), with the point and the button
/// as its payload (`StepClick`).
pub const STEP_CLICK_EVENT: &str = "capture:step-click";

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
    //! stop - and, for the guided capture of a walkthrough's steps (W2),
    //! Shift+F8 capture. `RegisterHotKey` binds a key to the THREAD that
    //! registers it and delivers `WM_HOTKEY` to that thread's message queue,
    //! so a thread of our own runs the queue while a recording (or a guided
    //! capture) lasts and raises the event in the main window; `stop` posts it
    //! `WM_QUIT`. The keys are taken only while recording or capturing, so the
    //! rest of the time they stay with whatever else wants them (ShareX,
    //! Snagit itself). One set at a time.
    //!
    //! The capture key's event carries the POINTER's position, read with
    //! `GetCursorPos` on this thread the moment `WM_HOTKEY` arrives - before
    //! the page is told, so before any still is taken - in physical
    //! virtual-screen pixels (this process is per-monitor DPI aware), the
    //! space the overlay's regions and the window list are already in. Read
    //! here rather than by a command the page calls back: the page learns of
    //! the press through an event anyway, and a round trip after it would
    //! read where the pointer went next.
    //!
    //! THE CLICK HOOK (W2b): in steps mode, when the page asks for it, this
    //! thread also installs a low-level mouse hook (`WH_MOUSE_LL`) - on THIS
    //! thread, whose message loop Windows calls it through - so that a click
    //! anywhere is a step. On `WM_LBUTTONDOWN` and `WM_RBUTTONDOWN` the
    //! callback reads the point from the hook struct, posts it to this
    //! thread's own queue (`WM_STEP_LEFT` / `WM_STEP_RIGHT`, x in wParam and y
    //! in lParam, `step_message` / `pack_point`) and returns `CallNextHookEx`
    //! AT ONCE: it never eats or delays a click and does nothing else there
    //! (a low-level hook that dawdles is removed by Windows without a word).
    //! Mouse-up, the wheel and a move are not posted. The loop then decides,
    //! from this process's OWN data and never by a hit-test (the window under
    //! a point in a Tauri window is WebView2's child, owned by another
    //! process, and `WindowFromPoint` would wait on whoever owns the window
    //! at the point): a press inside the visible frame of one of our
    //! top-level windows that can take a press (the bar, the overlays, the app
    //! window: `own_window_rects` - `EnumWindows`, `GetWindowThreadProcessId`
    //! against `GetCurrentProcessId`, then on our own handles only the
    //! visibility, cloak, click-through and minimised flags (`can_take_press`)
    //! and the DWM's visible frame, refreshed on each press; `point_in_any`)
    //! is ignored; so is the second press of a double click
    //! (`repeats_press`: the same button within `DOUBLE_CLICK_MS` of the
    //! previous EMITTED press, within `DOUBLE_CLICK_PX` of its point - the
    //! first press already took the still); everything else is raised to the
    //! main window as `capture:step-click` with the point and the button. The
    //! hook handle is owned by the state `stop` takes, so `stop` (and so
    //! `shutdown`) removes the hook itself before it quits the loop, and the
    //! thread removes it only when nobody else did. The recorder's key set
    //! never installs it (`hook_wanted`).
    use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
    use std::sync::{mpsc, Mutex};
    use std::time::Instant;

    use tauri::{AppHandle, Emitter};
    use windows::Win32::Foundation::{BOOL, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
    use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS};
    use windows::Win32::System::Threading::{GetCurrentProcessId, GetCurrentThreadId};
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        RegisterHotKey, UnregisterHotKey, MOD_NOREPEAT, MOD_SHIFT, VK_F10, VK_F8, VK_F9,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        CallNextHookEx, EnumWindows, GetCursorPos, GetMessageW, GetWindowLongPtrW, GetWindowRect, GetWindowThreadProcessId, IsIconic,
        IsWindowVisible, PeekMessageW, PostThreadMessageW, SetWindowsHookExW, UnhookWindowsHookEx, GWL_EXSTYLE, HHOOK, MSG,
        MSLLHOOKSTRUCT, PM_NOREMOVE, WH_MOUSE_LL, WM_HOTKEY, WM_QUIT, WM_USER, WS_EX_TRANSPARENT,
    };

    use super::{
        can_take_press, hook_wanted, keys_outcome, pack_point, point_in_any, repeats_press, step_message, unpack_point, ClickButton,
        KeySet, KeysUp, Pointer, StepClick, WindowFlags, WindowRect, WM_STEP_LEFT, WM_STEP_RIGHT,
    };

    const ID_PAUSE: i32 = 1;
    const ID_STOP: i32 = 2;
    const ID_CAPTURE: i32 = 3;

    /// What the thread holds while it runs: its id, the key set and, when
    /// the click hook was installed, its handle (as an integer: a raw
    /// pointer cannot sit in a static). Whoever takes it out unhooks it.
    struct Held {
        tid: u32,
        set: KeySet,
        hook: Option<isize>,
        /// Whether the set's key went up (H1: the guided capture's thread runs
        /// on for the hook alone when another program holds Shift+F8).
        key: bool,
    }
    static THREAD: Mutex<Option<Held>> = Mutex::new(None);
    /// Whether the click hook is installed now: set when `SetWindowsHookExW`
    /// answered a handle, cleared when `UnhookWindowsHookEx` answered Ok
    /// (`start`'s answer, and the tests' view of `stop`).
    static HOOK_UP: AtomicBool = AtomicBool::new(false);
    /// The HRESULT of the last start's refused `SetWindowsHookExW` (0 when it
    /// took, or was not tried): a low-level hook needs an interactive desktop,
    /// which a session-0 test host has not; the test says so instead of
    /// failing - and only when Windows really refused.
    static HOOK_ERROR: AtomicU32 = AtomicU32::new(0);

    /// What the thread raises; `start` maps it onto the main window's events.
    pub enum Raised {
        Hotkey(&'static str),
        StepKey(Option<Pointer>),
        StepClick(StepClick),
    }
    type Emit = Box<dyn Fn(Raised) + Send + 'static>;

    /// The pointer now, physical virtual-screen pixels; None when Windows
    /// cannot say (the page then captures without a hotspot).
    fn pointer() -> Option<Pointer> {
        let mut point = POINT::default();
        unsafe { GetCursorPos(&mut point).ok()? };
        Some(Pointer { x: point.x, y: point.y })
    }

    /// The flags `can_take_press` reads, from one of our own handles: its
    /// style bits and its visibility (no message is sent), and the DWM's cloak
    /// (a query of the compositor about our own window).
    unsafe fn flags_of(hwnd: HWND) -> WindowFlags {
        let ex = GetWindowLongPtrW(hwnd, GWL_EXSTYLE) as u32;
        let mut cloaked: u32 = 0;
        let cloak_known = DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, &mut cloaked as *mut _ as *mut core::ffi::c_void, 4).is_ok();
        WindowFlags {
            visible: IsWindowVisible(hwnd).as_bool(),
            cloaked: cloak_known && cloaked != 0,
            click_through: ex & WS_EX_TRANSPARENT.0 != 0,
            minimised: IsIconic(hwnd).as_bool(),
        }
    }

    /// The window's VISIBLE frame: the DWM's extended frame bounds, which
    /// leave out the invisible resize borders `GetWindowRect` includes (7 px a
    /// side on Windows 10/11), else `GetWindowRect` where the DWM cannot say.
    unsafe fn visible_frame(hwnd: HWND) -> Option<WindowRect> {
        let mut rect = RECT::default();
        let dwm = DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            &mut rect as *mut _ as *mut core::ffi::c_void,
            std::mem::size_of::<RECT>() as u32,
        );
        if dwm.is_err() && GetWindowRect(hwnd, &mut rect).is_err() {
            return None;
        }
        Some(WindowRect { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom })
    }

    /// The visible frames of THIS process's top-level windows that can take a
    /// press, now: `EnumWindows` walks the desktop's window list (no message to
    /// anyone), `GetWindowThreadProcessId` reads each window's owner against
    /// our pid, and only of our own handles are the flags (`can_take_press`:
    /// shown, not cloaked, not click-through, not minimised) and the visible
    /// frame read - a few windows, all ours, nothing that waits on another
    /// process.
    pub fn own_window_rects() -> Vec<WindowRect> {
        struct Walk {
            pid: u32,
            rects: Vec<WindowRect>,
        }
        unsafe extern "system" fn cb(hwnd: HWND, lparam: LPARAM) -> BOOL {
            let walk = &mut *(lparam.0 as *mut Walk);
            let mut pid = 0u32;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            if pid == walk.pid && can_take_press(flags_of(hwnd)) {
                if let Some(rect) = visible_frame(hwnd) {
                    walk.rects.push(rect);
                }
            }
            BOOL(1)
        }
        let mut walk = Walk { pid: unsafe { GetCurrentProcessId() }, rects: Vec::new() };
        unsafe {
            let _ = EnumWindows(Some(cb), LPARAM(&mut walk as *mut _ as isize));
        }
        walk.rects
    }

    /// The low-level mouse hook: a left or right press is posted to this
    /// thread's queue as the point alone (`step_message`, `pack_point`);
    /// everything goes on to the next hook at once, whatever it was.
    unsafe extern "system" fn mouse_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
        if code >= 0 {
            if let Some(message) = step_message(wparam.0 as u32) {
                let hit = &*(lparam.0 as *const MSLLHOOKSTRUCT);
                let (x, y) = pack_point(Pointer { x: hit.pt.x, y: hit.pt.y });
                let _ = PostThreadMessageW(GetCurrentThreadId(), message, WPARAM(x), LPARAM(y));
            }
        }
        CallNextHookEx(None, code, wparam, lparam)
    }

    /// Remove the hook: `UnhookWindowsHookEx`'s own answer, and `HOOK_UP`
    /// cleared only when Windows said Ok - so `hook_up()` after a `stop`
    /// says whether the hook is really gone.
    fn unhook(handle: isize) -> bool {
        let removed = unsafe { UnhookWindowsHookEx(HHOOK(handle as *mut core::ffi::c_void)).is_ok() };
        if removed {
            HOOK_UP.store(false, Ordering::SeqCst);
        }
        removed
    }

    fn keys_of(set: KeySet) -> &'static [(i32, u32)] {
        match set {
            KeySet::Recording => &[(ID_PAUSE, VK_F9.0 as u32), (ID_STOP, VK_F10.0 as u32)],
            KeySet::Steps => &[(ID_CAPTURE, VK_F8.0 as u32)],
        }
    }

    /// Take the set's keys - and, for the guided capture when `clicks` is
    /// asked, the click hook, whether or not the key went up - for the page.
    /// Answers which of the two is up (`KeysUp`, `keys_outcome`); Err for the
    /// recorder's set when its keys are taken, or when the other set is held.
    pub fn start(app: AppHandle, set: KeySet, clicks: bool) -> Result<KeysUp, String> {
        let emit: Emit = Box::new(move |raised| {
            let _ = match raised {
                Raised::Hotkey(action) => app.emit_to("main", super::HOTKEY_EVENT, action),
                Raised::StepKey(pointer) => app.emit_to("main", super::STEP_KEY_EVENT, pointer),
                Raised::StepClick(click) => app.emit_to("main", super::STEP_CLICK_EVENT, click),
            };
        });
        start_with(set, keys_of(set), hook_wanted(set, clicks), emit)
    }

    /// The thread itself, with the keys and the hook as given (the tests
    /// start it with no key - nothing taken from the desktop under test -
    /// and the hook alone).
    fn start_with(set: KeySet, ids: &'static [(i32, u32)], hook: bool, emit: Emit) -> Result<KeysUp, String> {
        let mut guard = THREAD.lock().map_err(|_| "hotkey state poisoned".to_string())?;
        if let Some(held) = guard.as_ref() {
            if held.set == set {
                return Ok(KeysUp { key: held.key, clicks: held.hook.is_some() });
            }
            return Err(match held.set {
                KeySet::Recording => "The recording keys are in use: stop the recording first.".into(),
                KeySet::Steps => "The capture key is in use: finish the guided capture first.".into(),
            });
        }
        let (tx, rx) = mpsc::channel::<Result<(u32, Option<isize>, KeysUp, bool), String>>();
        std::thread::spawn(move || unsafe {
            let tid = GetCurrentThreadId();
            let key_ok = ids
                .iter()
                .all(|(id, key)| RegisterHotKey(None, *id, MOD_SHIFT | MOD_NOREPEAT, *key).is_ok());
            if !key_ok {
                for (id, _) in ids {
                    let _ = UnregisterHotKey(None, *id);
                }
            }
            // The click hook, on THIS thread (Windows calls it through the loop below) - whether or not the key went
            // up: a Shift+F8 held by another program must not switch the clicks off (H1). Only ever asked for the
            // guided capture's set (`hook_wanted`). HOOK_ERROR speaks of this start only: 0 unless this install is refused.
            HOOK_ERROR.store(0, Ordering::SeqCst);
            let hhook = if hook {
                match SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_proc), None, 0) {
                    Ok(h) => {
                        HOOK_ERROR.store(0, Ordering::SeqCst);
                        Some(h.0 as isize)
                    }
                    Err(e) => {
                        HOOK_ERROR.store(e.code().0 as u32, Ordering::SeqCst);
                        None
                    }
                }
            } else {
                None
            };
            let (up, runs_on) = match keys_outcome(set, key_ok, hhook.is_some()) {
                Ok(outcome) => outcome,
                Err(()) => {
                    if let Some(handle) = hhook {
                        let _ = unhook(handle);
                    }
                    let _ = tx.send(Err(match set {
                        KeySet::Recording => "Shift+F9 / Shift+F10 are taken by another program, so the recording keys are off.".into(),
                        KeySet::Steps => "Shift+F8 is taken by another program, so the capture key is off.".into(),
                    }));
                    return;
                }
            };
            HOOK_UP.store(hhook.is_some(), Ordering::SeqCst);
            if !runs_on {
                // Nothing went up (the key taken, the hook refused or not asked): nothing to run a loop for.
                let _ = tx.send(Ok((tid, None, up, false)));
                return;
            }
            // The thread's message queue made before its id is handed out (the documented idiom): with no key and
            // no hook nothing else has made it yet, and a post that arrives first - `stop`'s WM_QUIT - is refused.
            let mut peek = MSG::default();
            let _ = PeekMessageW(&mut peek, None, WM_USER, WM_USER, PM_NOREMOVE);
            let _ = tx.send(Ok((tid, hhook, up, true)));
            let clock = Instant::now();
            let mut last_emitted: Option<(Pointer, ClickButton, u64)> = None;
            let mut msg = MSG::default();
            while GetMessageW(&mut msg, None, 0, 0).as_bool() {
                match msg.message {
                    WM_HOTKEY => match msg.wParam.0 as i32 {
                        ID_PAUSE => emit(Raised::Hotkey("pause")),
                        ID_STOP => emit(Raised::Hotkey("stop")),
                        // The pointer first, then the page: nothing has moved yet.
                        ID_CAPTURE => emit(Raised::StepKey(pointer())),
                        _ => continue,
                    },
                    WM_STEP_LEFT | WM_STEP_RIGHT => {
                        let point = unpack_point(msg.wParam.0, msg.lParam.0);
                        let button = if msg.message == WM_STEP_LEFT { ClickButton::Left } else { ClickButton::Right };
                        let now = clock.elapsed().as_millis() as u64;
                        if point_in_any(&own_window_rects(), point) || repeats_press(last_emitted, point, button, now) {
                            continue;
                        }
                        last_emitted = Some((point, button, now));
                        emit(Raised::StepClick(StepClick { x: point.x, y: point.y, button }));
                    }
                    _ => continue,
                }
            }
            if key_ok {
                for (id, _) in ids {
                    let _ = UnregisterHotKey(None, *id);
                }
            }
            // The hook, when `stop` has not taken it already (it owns what the state holds).
            if let Ok(mut guard) = THREAD.lock() {
                if guard.as_ref().map_or(false, |held| held.tid == tid) {
                    if let Some(handle) = guard.take().and_then(|held| held.hook) {
                        let _ = unhook(handle);
                    }
                }
            }
        });
        match rx.recv() {
            Ok(Ok((tid, hhook, up, runs_on))) => {
                if runs_on {
                    *guard = Some(Held { tid, set, hook: hhook, key: up.key });
                }
                Ok(up)
            }
            Ok(Err(e)) => Err(e),
            Err(_) => Err("The hotkey thread did not start.".into()),
        }
    }

    /// Give the keys back: the click hook removed, then the thread told to
    /// quit, which unregisters the keys. Nothing held is nothing done - safe
    /// from the page's own release and from the main window's shutdown after
    /// it, in either order, and twice over.
    pub fn stop() {
        if let Ok(mut guard) = THREAD.lock() {
            if let Some(held) = guard.take() {
                if let Some(handle) = held.hook {
                    let _ = unhook(handle);
                }
                unsafe {
                    let _ = PostThreadMessageW(held.tid, WM_QUIT, WPARAM(0), LPARAM(0));
                }
            }
        }
    }

    /// The set held now, if any (the tests' view of `stop`).
    #[cfg(test)]
    pub fn held() -> Option<KeySet> {
        THREAD.lock().ok().and_then(|guard| guard.as_ref().map(|held| held.set))
    }

    /// Whether the click hook is installed now, as Windows last answered
    /// (the tests' view of `stop`).
    #[cfg(test)]
    pub fn hook_up() -> bool {
        HOOK_UP.load(Ordering::SeqCst)
    }

    /// The HRESULT of the last refused hook install, 0 when it took.
    #[cfg(test)]
    pub fn hook_error() -> u32 {
        HOOK_ERROR.load(Ordering::SeqCst)
    }

    /// The real thread with NO key registered (nothing taken from the
    /// desktop the tests run on) and the hook as `hook_wanted` decides;
    /// what it raises goes nowhere.
    #[cfg(test)]
    pub fn start_for_test(set: KeySet, clicks: bool) -> Result<KeysUp, String> {
        start_with(set, &[], hook_wanted(set, clicks), Box::new(|_| {}))
    }

    /// The real thread with the keys given (Shift + each key, as the real
    /// sets register them) and the hook as `hook_wanted` decides - for a key
    /// the test itself holds first, so the refusal path runs for real.
    #[cfg(test)]
    pub fn start_for_test_keys(set: KeySet, ids: &'static [(i32, u32)], clicks: bool) -> Result<KeysUp, String> {
        start_with(set, ids, hook_wanted(set, clicks), Box::new(|_| {}))
    }

    /// The real thread with no key and no hook, what it raises sent to `tx`:
    /// the loop's own rules, fed by `post_press_for_test`.
    #[cfg(test)]
    pub fn start_for_test_emitting(set: KeySet, tx: mpsc::Sender<Raised>) -> Result<KeysUp, String> {
        start_with(set, &[], false, Box::new(move |raised| {
            let _ = tx.send(raised);
        }))
    }

    /// A press handed to the running thread's loop exactly as the hook hands
    /// it over (`step_message`'s message, `pack_point`'s words).
    #[cfg(test)]
    pub fn post_press_for_test(point: Pointer, button: ClickButton) -> bool {
        let Some(tid) = THREAD.lock().ok().and_then(|guard| guard.as_ref().map(|held| held.tid)) else { return false };
        let message = if button == ClickButton::Left { WM_STEP_LEFT } else { WM_STEP_RIGHT };
        let (x, y) = pack_point(point);
        unsafe { PostThreadMessageW(tid, message, WPARAM(x), LPARAM(y)).is_ok() }
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
    pub fn start(_app: AppHandle, _set: super::KeySet, _clicks: bool) -> Result<super::KeysUp, String> {
        Err("Recording hotkeys are available on Windows only.".into())
    }
    pub fn stop() {}
    #[cfg(test)]
    pub fn held() -> Option<super::KeySet> {
        None
    }
    #[cfg(test)]
    pub fn hook_up() -> bool {
        false
    }
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

/// Open the floating capture bar: a small undecorated always-on-top window
/// at the backend's `capture-bar.html` (built here from the backend's
/// origin, like the overlay's), placed at physical `x`,`y`
/// with the given physical size, and excluded from screen capture so it
/// never appears in a recording wherever it sits (`affinity`). `mode` picks
/// the bar's page mode (`BarMode`): the recorder's by default, the guided
/// capture's with `"steps"` (W2). Returns whether the exclusion took: false
/// on a Windows older than 10 2004, where the bar is in the picture and the
/// UI should say so.
#[tauri::command(async)]
pub fn capture_bar_open(app: AppHandle, x: i32, y: i32, width: u32, height: u32, mode: Option<String>) -> Result<bool, String> {
    let mode = BarMode::parse(mode.as_deref())?;
    let url: tauri::Url = bar_url(&current_origin()?, mode).parse().map_err(|e| format!("Bad bar URL: {e}"))?;
    capture_bar_close(app.clone());
    let window = WebviewWindowBuilder::new(&app, BAR_LABEL, WebviewUrl::External(url))
        .title(mode.title())
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
        .map_err(|e| format!("Could not open the {}: {e}", mode.bar_name()))?;
    window
        .set_position(PhysicalPosition::new(x, y))
        .and_then(|_| window.set_size(PhysicalSize::new(width, height)))
        .map_err(|e| format!("Could not place the {}: {e}", mode.bar_name()))?;
    #[cfg(windows)]
    let excluded = window
        .hwnd()
        .map(|h| affinity::exclude(h.0 as *mut core::ffi::c_void))
        .unwrap_or(false);
    #[cfg(not(windows))]
    let excluded = false;
    window.show().map_err(|e| format!("Could not show the {}: {e}", mode.bar_name()))?;
    Ok(excluded)
}

/// Close the recorder bar if it is open.
#[tauri::command(async)]
pub fn capture_bar_close(app: AppHandle) {
    if let Some(window) = app.get_webview_window(BAR_LABEL) {
        let _ = window.close();
    }
}

/// Take the global keys for the length of a recording or a guided capture:
/// the recording keys by default (Shift+F9 pause/resume, Shift+F10 stop;
/// `capture:hotkey` raised in the main window with `"pause"` or `"stop"`), or
/// with `keys: "steps"` the capture key (Shift+F8; `capture:step-key` raised
/// with the pointer's position, read as the key is pressed) - and, with
/// `clicks: true` in steps mode, the click hook (`capture:step-click` raised
/// for every left or right press outside this process's windows, W2b; the
/// recorder's set never installs it), tried whether or not Shift+F8 went up.
/// Answers `{ key, clicks }` (`KeysUp`): which of the two is up, so the page
/// can say which is off. Err when another program holds the recording keys,
/// or the other set is in use.
#[tauri::command(async)]
pub fn capture_hotkeys_start(app: AppHandle, keys: Option<String>, clicks: Option<bool>) -> Result<KeysUp, String> {
    hotkeys::start(app, KeySet::parse(keys.as_deref())?, clicks.unwrap_or(false))
}

/// Give the keys back.
#[tauri::command(async)]
pub fn capture_hotkeys_stop() {
    hotkeys::stop();
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The hotkey thread's state is one static: the tests that touch it run one at a time.
    static HOTKEY_TESTS: Mutex<()> = Mutex::new(());

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
        assert_eq!(bar_url(&origin, BarMode::Recording), "http://127.0.0.1:51234/capture-bar.html");
        assert_eq!(bar_url(&origin, BarMode::Steps), "http://127.0.0.1:51234/capture-bar.html?mode=steps");
        let parsed: tauri::Url = overlay_url(&origin, 0, 1).parse().unwrap();
        assert_eq!((parsed.host_str(), parsed.port()), (Some("127.0.0.1"), Some(51234)));
        let steps: tauri::Url = bar_url(&origin, BarMode::Steps).parse().unwrap();
        assert_eq!((steps.host_str(), steps.port(), steps.query()), (Some("127.0.0.1"), Some(51234), Some("mode=steps")));
    }

    #[test]
    fn the_bars_second_mode_and_the_capture_key_set_are_named_by_the_page() {
        // The bar: the recorder's by default, the guided capture's with "steps"; anything else refused.
        assert_eq!(BarMode::parse(None), Ok(BarMode::Recording));
        assert_eq!(BarMode::parse(Some("")), Ok(BarMode::Recording));
        assert_eq!(BarMode::parse(Some("recording")), Ok(BarMode::Recording));
        assert_eq!(BarMode::parse(Some("steps")), Ok(BarMode::Steps));
        assert!(BarMode::parse(Some("other")).is_err());
        assert_eq!((BarMode::Recording.title(), BarMode::Steps.title()), ("Recording", "Capture steps"));
        // The keys: the recorder's two by default, the capture key with "steps".
        assert_eq!(KeySet::parse(None), Ok(KeySet::Recording));
        assert_eq!(KeySet::parse(Some("recording")), Ok(KeySet::Recording));
        assert_eq!(KeySet::parse(Some("steps")), Ok(KeySet::Steps));
        assert!(KeySet::parse(Some("f8")).is_err());
        // The key's event carries the pointer as {x, y}, in physical pixels, as the page reads it (lib/guided.ts pointerOf).
        assert_eq!(STEP_KEY_EVENT, "capture:step-key");
        assert_eq!(serde_json::to_value(Pointer { x: -1920, y: 42 }).unwrap(), serde_json::json!({ "x": -1920, "y": 42 }));
        assert_eq!(serde_json::to_value(Option::<Pointer>::None).unwrap(), serde_json::Value::Null);
    }

    #[test]
    fn giving_the_keys_back_with_nothing_held_is_a_no_op_twice_over() {
        let _serial = HOTKEY_TESTS.lock().unwrap_or_else(|e| e.into_inner());
        // The main window's Destroyed branch runs `shutdown` after the page may already have given the keys back
        // (Done, Stop), or when nothing was ever taken: `hotkeys::stop` must be safe with nothing held, and again
        // after that. (The bar's close and the overlays' are the window's own close, idempotent by Tauri.)
        assert_eq!(hotkeys::held(), None);
        hotkeys::stop();
        assert_eq!(hotkeys::held(), None);
        hotkeys::stop();
        assert_eq!(hotkeys::held(), None);
        assert!(!hotkeys::hook_up());
    }

    #[cfg(windows)]
    #[test]
    fn giving_the_keys_back_removes_the_click_hook_and_is_a_no_op_after_that() {
        let _serial = HOTKEY_TESTS.lock().unwrap_or_else(|e| e.into_inner());
        // The real thread, no key registered (nothing taken from the desktop the tests run on), the click hook alone.
        let both = KeysUp { key: true, clicks: true };
        let first = hotkeys::start_for_test(KeySet::Steps, true);
        // Skipped only when Windows itself refused the hook (an HRESULT), never because the hook was not tried.
        if matches!(first, Ok(KeysUp { clicks: false, .. })) && hotkeys::hook_error() != 0 {
            // A low-level hook needs an interactive desktop: a session-0 host (a service, some CI) is refused it.
            hotkeys::stop();
            if std::env::var("MEDIA_STUDIO_HOOK_TEST").as_deref() != Ok("require") {
                eprintln!(
                    "skipped: SetWindowsHookExW refused the low-level mouse hook (HRESULT 0x{:08x}) - no interactive desktop? Set MEDIA_STUDIO_HOOK_TEST=require to fail instead.",
                    hotkeys::hook_error()
                );
                return;
            }
        }
        assert_eq!(first, Ok(both), "the hook could not be installed (HRESULT 0x{:08x})", hotkeys::hook_error());
        assert_eq!(hotkeys::held(), Some(KeySet::Steps));
        assert!(hotkeys::hook_up(), "the hook is installed with the steps set when the page asks");
        assert_eq!(hotkeys::hook_error(), 0);
        // The own-window list is read from this process's data: the test process has no window, so no point is in it.
        assert_eq!(hotkeys::own_window_rects(), Vec::<WindowRect>::new());
        assert!(!point_in_any(&hotkeys::own_window_rects(), Pointer { x: 0, y: 0 }));
        // A second start of the same set answers what is held: nothing is installed twice.
        assert_eq!(hotkeys::start_for_test(KeySet::Steps, true), Ok(both));
        assert_eq!(hotkeys::start_for_test(KeySet::Recording, false).ok(), None, "the other set is refused while this one is held");
        // Stop: the hook is removed by stop itself - `hook_up` clears only on UnhookWindowsHookEx's Ok, so this says
        // Windows really removed it - and the state is cleared; again: nothing held, nothing done.
        hotkeys::stop();
        assert_eq!((hotkeys::held(), hotkeys::hook_up()), (None, false));
        hotkeys::stop();
        assert_eq!((hotkeys::held(), hotkeys::hook_up()), (None, false));
        // And the thread starts anew after that.
        assert_eq!(hotkeys::start_for_test(KeySet::Steps, true), Ok(both));
        assert!(hotkeys::hook_up());
        hotkeys::stop();
        assert_eq!((hotkeys::held(), hotkeys::hook_up()), (None, false));
        // Without the ask, and for the recorder's set with it: the keys alone, no hook (clicks: false, not asked).
        let key_only = KeysUp { key: true, clicks: false };
        assert_eq!(hotkeys::start_for_test(KeySet::Steps, false), Ok(key_only));
        assert!(!hotkeys::hook_up());
        hotkeys::stop();
        assert_eq!(hotkeys::start_for_test(KeySet::Recording, true), Ok(key_only));
        assert!(!hotkeys::hook_up(), "the recorder's set never installs the click hook");
        hotkeys::stop();
        assert_eq!((hotkeys::held(), hotkeys::hook_up()), (None, false));
    }

    #[test]
    fn a_taken_capture_key_never_switches_the_clicks_off_and_the_answer_names_both() {
        let up = |key, clicks| KeysUp { key, clicks };
        // The guided capture's set answers both facts, whatever they are, and runs on while either is up (H1).
        assert_eq!(keys_outcome(KeySet::Steps, true, true), Ok((up(true, true), true)));
        assert_eq!(keys_outcome(KeySet::Steps, false, true), Ok((up(false, true), true)), "Shift+F8 taken: the clicks still go");
        assert_eq!(keys_outcome(KeySet::Steps, true, false), Ok((up(true, false), true)), "the hook refused or not asked: the key goes");
        assert_eq!(keys_outcome(KeySet::Steps, false, false), Ok((up(false, false), false)), "nothing up: the thread ends");
        // The recorder's set is its keys or nothing, as before: taken keys refuse it.
        assert_eq!(keys_outcome(KeySet::Recording, false, false), Err(()));
        assert_eq!(keys_outcome(KeySet::Recording, true, false), Ok((up(true, false), true)));
        // What the page reads: {key, clicks}.
        assert_eq!(serde_json::to_value(up(false, true)).unwrap(), serde_json::json!({ "key": false, "clicks": true }));
    }

    #[cfg(windows)]
    #[test]
    fn with_the_capture_key_held_elsewhere_the_hook_still_goes_up_and_the_answer_says_so() {
        use windows::Win32::UI::Input::KeyboardAndMouse::{RegisterHotKey, UnregisterHotKey, MOD_NOREPEAT, MOD_SHIFT, VK_F24};
        let _serial = HOTKEY_TESTS.lock().unwrap_or_else(|e| e.into_inner());
        // The test holds Shift+F24 itself (no keyboard has an F24 key: nothing of anyone's is taken), so the thread's
        // own registration of the same key is refused for real - "another program holds the key".
        const TAKEN: &[(i32, u32)] = &[(3, VK_F24.0 as u32)];
        // Another program already holding Shift+F24 says nothing about the code under test: the test cannot set up
        // its "taken" key, so it says so and passes, unless MEDIA_STUDIO_HOOK_TEST=require.
        if let Err(e) = unsafe { RegisterHotKey(None, 0x2B24, MOD_SHIFT | MOD_NOREPEAT, VK_F24.0 as u32) } {
            if std::env::var("MEDIA_STUDIO_HOOK_TEST").as_deref() == Ok("require") {
                panic!("the test could not take Shift+F24 first ({e}), and MEDIA_STUDIO_HOOK_TEST=require");
            }
            eprintln!("skipped: the test could not take Shift+F24 first ({e}) - another program holds it? Set MEDIA_STUDIO_HOOK_TEST=require to fail instead.");
            return;
        }
        let steps = hotkeys::start_for_test_keys(KeySet::Steps, TAKEN, true);
        // Windows itself refused the hook (an HRESULT): the hook half is skipped. A hook not tried is no reason to skip.
        let hook_refused = matches!(steps, Ok(KeysUp { clicks: false, .. })) && hotkeys::hook_error() != 0;
        if !hook_refused {
            // The key refused, the hook up anyway: clicks are captured whoever holds Shift+F8.
            assert_eq!(steps, Ok(KeysUp { key: false, clicks: true }));
            assert_eq!(hotkeys::held(), Some(KeySet::Steps), "the thread runs on for the hook alone");
            assert!(hotkeys::hook_up());
            hotkeys::stop();
            assert_eq!((hotkeys::held(), hotkeys::hook_up()), (None, false));
        } else if std::env::var("MEDIA_STUDIO_HOOK_TEST").as_deref() == Ok("require") {
            panic!("the hook could not be installed (HRESULT 0x{:08x})", hotkeys::hook_error());
        } else {
            eprintln!("skipped the hook half: SetWindowsHookExW refused (HRESULT 0x{:08x}) - no interactive desktop?", hotkeys::hook_error());
        }
        // The key refused and no hook asked: both off, nothing held, no thread left.
        assert_eq!(hotkeys::start_for_test_keys(KeySet::Steps, TAKEN, false), Ok(KeysUp { key: false, clicks: false }));
        assert_eq!(hotkeys::held(), None);
        // The recorder's set with its key taken: refused, as always.
        assert!(hotkeys::start_for_test_keys(KeySet::Recording, TAKEN, false).is_err());
        assert_eq!(hotkeys::held(), None);
        unsafe {
            let _ = UnregisterHotKey(None, 0x2B24);
        }
        // Freed: the same key goes up.
        assert_eq!(hotkeys::start_for_test_keys(KeySet::Steps, TAKEN, false), Ok(KeysUp { key: true, clicks: false }));
        assert_eq!(hotkeys::held(), Some(KeySet::Steps));
        hotkeys::stop();
        assert_eq!(hotkeys::held(), None);
    }

    #[test]
    fn the_click_hook_is_the_guided_captures_alone() {
        assert!(hook_wanted(KeySet::Steps, true));
        assert!(!hook_wanted(KeySet::Steps, false));
        assert!(!hook_wanted(KeySet::Recording, true), "the recorder's key set never installs the hook");
        assert!(!hook_wanted(KeySet::Recording, false));
    }

    #[test]
    fn the_second_press_of_a_double_click_is_not_a_step() {
        use ClickButton::{Left, Right};
        let at = Pointer { x: 400, y: 300 };
        // Nothing emitted yet: every press is a step.
        assert!(!repeats_press(None, at, Left, 0));
        // The same button within 400 ms and within 8 px on both axes of the previous emitted press: the second press of a double click.
        assert!(repeats_press(Some((at, Left, 1000)), at, Left, 1000));
        assert!(repeats_press(Some((at, Left, 1000)), Pointer { x: 408, y: 292 }, Left, 1400));
        assert!(repeats_press(Some((at, Right, 1000)), Pointer { x: 392, y: 308 }, Right, 1399));
        // A moment later, or a little further: a step.
        assert!(!repeats_press(Some((at, Left, 1000)), at, Left, 1401));
        assert!(!repeats_press(Some((at, Left, 1000)), Pointer { x: 409, y: 300 }, Left, 1200));
        assert!(!repeats_press(Some((at, Left, 1000)), Pointer { x: 400, y: 291 }, Left, 1200));
        // The OTHER button on the heels of a press is a step: "click the step, then right-click it" (the lab's steps 3 and 6).
        assert!(!repeats_press(Some((at, Left, 1000)), at, Right, 1100));
        assert!(!repeats_press(Some((at, Right, 1000)), at, Left, 1100));
        assert_eq!((DOUBLE_CLICK_MS, DOUBLE_CLICK_PX), (400, 8));
    }

    #[test]
    fn a_press_on_one_of_our_own_windows_is_not_a_step() {
        // The bar (undecorated, 600 x 112 at 768,1265), the app window minimised (Windows reports -32000 for it).
        let bar = WindowRect { left: 768, top: 1265, right: 1368, bottom: 1377 };
        let minimised = WindowRect { left: -32000, top: -32000, right: -31840, bottom: -31972 };
        let ours = [bar, minimised];
        assert!(point_in_any(&ours, Pointer { x: 768, y: 1265 }), "the top-left corner is inside");
        assert!(point_in_any(&ours, Pointer { x: 1000, y: 1300 }));
        assert!(point_in_any(&ours, Pointer { x: 1367, y: 1376 }), "the last pixel is inside");
        // Half-open: the right and bottom edges are outside, as is everything around.
        assert!(!point_in_any(&ours, Pointer { x: 1368, y: 1300 }));
        assert!(!point_in_any(&ours, Pointer { x: 1000, y: 1377 }));
        assert!(!point_in_any(&ours, Pointer { x: 767, y: 1300 }));
        assert!(!point_in_any(&ours, Pointer { x: 1000, y: 1264 }));
        // A minimised window's rectangle holds no point on any monitor; another program's window is not in the list at all.
        assert!(!point_in_any(&ours, Pointer { x: 0, y: 0 }));
        assert!(!point_in_any(&ours, Pointer { x: -1920, y: 42 }));
        assert!(!point_in_any(&[], Pointer { x: 1000, y: 1300 }), "no window of ours: every press goes on to the page's rules");
    }

    #[test]
    fn a_window_takes_a_press_only_when_shown_uncloaked_not_click_through_and_not_minimised() {
        let shown = WindowFlags { visible: true, ..WindowFlags::default() };
        assert!(can_take_press(shown), "a shown window of ours takes the press: not a step");
        // Each flag on its own keeps the window out of the list, so a press over it is a step.
        assert!(!can_take_press(WindowFlags { visible: false, ..shown }), "hidden");
        assert!(!can_take_press(WindowFlags { cloaked: true, ..shown }), "cloaked");
        assert!(!can_take_press(WindowFlags { click_through: true, ..shown }), "click-through (tao's event-target window)");
        assert!(!can_take_press(WindowFlags { minimised: true, ..shown }), "minimised");
        assert!(!can_take_press(WindowFlags::default()));
    }

    /// Windows of the test process for the own-window tests: off every monitor (at -20000), shown without
    /// activation, a tool window (no taskbar button) - nothing appears on the desktop the tests run on.
    #[cfg(windows)]
    mod probe {
        use super::WindowRect;
        use windows::core::w;
        use windows::Win32::Foundation::{BOOL, HWND, RECT};
        use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DwmSetWindowAttribute, DWMWA_CLOAK, DWMWA_EXTENDED_FRAME_BOUNDS};
        use windows::Win32::UI::WindowsAndMessaging::{
            CreateWindowExW, DestroyWindow, GetWindowRect, ShowWindow, SW_SHOWNOACTIVATE, WINDOW_EX_STYLE, WINDOW_STYLE, WS_EX_NOACTIVATE,
            WS_EX_TOOLWINDOW,
        };

        pub struct Probe(pub HWND);
        impl Drop for Probe {
            fn drop(&mut self) {
                unsafe {
                    let _ = DestroyWindow(self.0);
                }
            }
        }

        pub fn window(x: i32, y: i32, width: i32, height: i32, style: WINDOW_STYLE, ex: WINDOW_EX_STYLE, show: bool) -> Probe {
            let hwnd = unsafe {
                CreateWindowExW(ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, w!("STATIC"), w!("w2b probe"), style, x, y, width, height, None, None, None, None)
            }
            .expect("CreateWindowExW");
            if show {
                unsafe {
                    let _ = ShowWindow(hwnd, SW_SHOWNOACTIVATE);
                }
            }
            Probe(hwnd)
        }

        pub fn cloak(p: &Probe) -> bool {
            let on = BOOL(1);
            unsafe { DwmSetWindowAttribute(p.0, DWMWA_CLOAK, &on as *const _ as *const core::ffi::c_void, 4).is_ok() }
        }

        fn of(r: RECT) -> WindowRect {
            WindowRect { left: r.left, top: r.top, right: r.right, bottom: r.bottom }
        }

        pub fn window_rect(p: &Probe) -> WindowRect {
            let mut r = RECT::default();
            unsafe { GetWindowRect(p.0, &mut r).expect("GetWindowRect") };
            of(r)
        }

        pub fn dwm_frame(p: &Probe) -> Option<WindowRect> {
            let mut r = RECT::default();
            unsafe {
                DwmGetWindowAttribute(p.0, DWMWA_EXTENDED_FRAME_BOUNDS, &mut r as *mut _ as *mut core::ffi::c_void, std::mem::size_of::<RECT>() as u32)
                    .ok()?
            };
            Some(of(r))
        }

        /// No DWM (a session-0 host): the test says so and passes, unless MEDIA_STUDIO_HOOK_TEST=require.
        pub fn no_desktop(visible: &Probe) -> bool {
            if dwm_frame(visible).is_some() {
                return false;
            }
            if std::env::var("MEDIA_STUDIO_HOOK_TEST").as_deref() == Ok("require") {
                panic!("the DWM cannot report a window's frame here, and MEDIA_STUDIO_HOOK_TEST=require");
            }
            eprintln!("skipped: the DWM cannot report a window's frame (no interactive desktop?). Set MEDIA_STUDIO_HOOK_TEST=require to fail instead.");
            true
        }
    }

    #[cfg(windows)]
    #[test]
    fn only_our_windows_that_can_take_a_press_are_listed_by_their_visible_frame() {
        use windows::Win32::UI::WindowsAndMessaging::{WINDOW_EX_STYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_OVERLAPPEDWINDOW, WS_POPUP};
        let _serial = HOTKEY_TESTS.lock().unwrap_or_else(|e| e.into_inner());
        let none = WINDOW_EX_STYLE(0);
        let shown = probe::window(-20000, -20000, 100, 100, WS_POPUP, none, true);
        if probe::no_desktop(&shown) {
            return;
        }
        let hidden = probe::window(-20000, -19000, 100, 100, WS_POPUP, none, false);
        // tao's event-target window's shape: visible, layered, click-through.
        let click_through = probe::window(-20000, -18000, 100, 100, WS_POPUP, WS_EX_LAYERED | WS_EX_TRANSPARENT, true);
        let cloaked = probe::window(-20000, -17000, 100, 100, WS_POPUP, none, true);
        assert!(probe::cloak(&cloaked), "DwmSetWindowAttribute(DWMWA_CLOAK) on our own window");
        // A framed window: its visible frame is inside the rectangle GetWindowRect reports (the invisible resize borders).
        let framed = probe::window(-20000, -16000, 400, 300, WS_OVERLAPPEDWINDOW, none, true);
        let listed = hotkeys::own_window_rects();
        let shown_rect = probe::window_rect(&shown);
        assert_eq!(shown_rect, WindowRect { left: -20000, top: -20000, right: -19900, bottom: -19900 });
        assert!(listed.contains(&shown_rect), "the shown window is listed: {listed:?}");
        for (what, p) in [("hidden", &hidden), ("click-through", &click_through), ("cloaked", &cloaked)] {
            let r = probe::window_rect(p);
            assert!(!listed.iter().any(|l| l.top == r.top), "the {what} window is not listed: {listed:?}");
        }
        let outer = probe::window_rect(&framed);
        let frame = probe::dwm_frame(&framed).expect("the DWM's frame");
        assert!(listed.contains(&frame), "the framed window is listed by its visible frame {frame:?}: {listed:?}");
        assert!(
            frame.left > outer.left && frame.right < outer.right && frame.bottom < outer.bottom && frame.top >= outer.top,
            "the visible frame {frame:?} leaves out the invisible borders of {outer:?}"
        );
        assert!(!listed.contains(&outer), "never the outer rectangle, borders and all");
        assert_eq!(listed.len(), 2, "the shown and the framed window, nothing else of the test process: {listed:?}");
        drop((shown, hidden, click_through, cloaked, framed));
        assert_eq!(hotkeys::own_window_rects(), Vec::<WindowRect>::new(), "destroyed: gone from the list");
    }

    #[cfg(windows)]
    #[test]
    fn the_loop_drops_a_press_on_our_window_and_raises_a_press_anywhere_else() {
        use windows::Win32::UI::WindowsAndMessaging::{WINDOW_EX_STYLE, WS_POPUP};
        let _serial = HOTKEY_TESTS.lock().unwrap_or_else(|e| e.into_inner());
        let ours = probe::window(-20000, -20000, 100, 100, WS_POPUP, WINDOW_EX_STYLE(0), true);
        if probe::no_desktop(&ours) {
            return;
        }
        let (tx, rx) = std::sync::mpsc::channel::<hotkeys::Raised>();
        assert_eq!(hotkeys::start_for_test_emitting(KeySet::Steps, tx), Ok(KeysUp { key: true, clicks: false }));
        let inside = Pointer { x: -19950, y: -19950 };
        let elsewhere = Pointer { x: -15000, y: -15000 };
        // The two presses as the hook hands them over: inside our window first, then elsewhere.
        assert!(hotkeys::post_press_for_test(inside, ClickButton::Left));
        assert!(hotkeys::post_press_for_test(elsewhere, ClickButton::Right));
        let first = rx.recv_timeout(std::time::Duration::from_secs(2));
        let clicked = |r: &Result<hotkeys::Raised, _>| match r {
            Ok(hotkeys::Raised::StepClick(c)) => Some(*c),
            _ => None,
        };
        assert_eq!(clicked(&first), Some(StepClick { x: -15000, y: -15000, button: ClickButton::Right }), "only the press elsewhere is raised");
        assert!(rx.recv_timeout(std::time::Duration::from_millis(300)).is_err(), "the press on our window raised nothing");
        // The list is read on each press: our window gone, the same point is a step.
        drop(ours);
        assert!(hotkeys::post_press_for_test(inside, ClickButton::Left));
        let after = rx.recv_timeout(std::time::Duration::from_secs(2));
        assert_eq!(clicked(&after), Some(StepClick { x: -19950, y: -19950, button: ClickButton::Left }));
        hotkeys::stop();
        assert_eq!(hotkeys::held(), None);
    }

    #[test]
    fn the_callback_posts_a_left_or_right_press_and_nothing_else() {
        // The hook's wParam is the mouse message: a left or right press is posted as the thread's own message …
        assert_eq!(step_message(0x0201), Some(WM_STEP_LEFT));
        assert_eq!(step_message(0x0204), Some(WM_STEP_RIGHT));
        // … and mouse-up, a move, the wheel, the middle and the X buttons are never posted.
        for other in [0x0202u32, 0x0205, 0x0200, 0x020A, 0x020E, 0x0207, 0x0208, 0x020B, 0x020C, 0] {
            assert_eq!(step_message(other), None, "0x{other:04x}");
        }
        assert_ne!(WM_STEP_LEFT, WM_STEP_RIGHT);
        // The values are the ones Windows names (the `windows` crate's constants), so the pure rule reads the real hook.
        #[cfg(windows)]
        {
            use windows::Win32::UI::WindowsAndMessaging::{WM_APP, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_RBUTTONDOWN};
            assert_eq!(step_message(WM_LBUTTONDOWN), Some(WM_STEP_LEFT));
            assert_eq!(step_message(WM_RBUTTONDOWN), Some(WM_STEP_RIGHT));
            assert_eq!((step_message(WM_LBUTTONUP), step_message(WM_MOUSEMOVE), step_message(WM_MOUSEWHEEL)), (None, None, None));
            assert_eq!((WM_STEP_LEFT, WM_STEP_RIGHT), (WM_APP + 0x21, WM_APP + 0x22));
        }
    }

    #[test]
    fn the_point_survives_the_posted_messages_two_words() {
        // x = -1920 (a monitor left of the primary) through wParam, y through lParam, on either pointer width.
        for point in [
            Pointer { x: -1920, y: 42 },
            Pointer { x: 7, y: -1080 },
            Pointer { x: 0, y: 0 },
            Pointer { x: i32::MAX, y: i32::MIN },
            Pointer { x: i32::MIN, y: i32::MAX },
            Pointer { x: 2559, y: 1439 },
        ] {
            let (wparam, lparam) = pack_point(point);
            assert_eq!(unpack_point(wparam, lparam), point, "{point:?}");
        }
        // The words themselves: x zero-extended (no sign bits above 32), y sign-extended.
        let (wparam, lparam) = pack_point(Pointer { x: -1920, y: -1 });
        assert_eq!(wparam, 0xFFFF_F880);
        assert_eq!(lparam, -1);
    }

    #[test]
    fn the_click_event_carries_the_point_and_the_button() {
        // The page reads {x, y, button} (lib/guided.ts clickOf): the same physical pixels as the key's pointer.
        assert_eq!(STEP_CLICK_EVENT, "capture:step-click");
        assert_eq!(
            serde_json::to_value(StepClick { x: -1920, y: 42, button: ClickButton::Left }).unwrap(),
            serde_json::json!({ "x": -1920, "y": 42, "button": "left" })
        );
        assert_eq!(
            serde_json::to_value(StepClick { x: 7, y: 9, button: ClickButton::Right }).unwrap(),
            serde_json::json!({ "x": 7, "y": 9, "button": "right" })
        );
    }

    #[test]
    fn the_bar_is_named_by_its_mode_in_the_shells_refusals() {
        // D2: "Could not open the capture bar: …" in steps mode, never "recorder bar" there.
        assert_eq!(BarMode::Recording.bar_name(), "recorder bar");
        assert_eq!(BarMode::Steps.bar_name(), "capture bar");
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
