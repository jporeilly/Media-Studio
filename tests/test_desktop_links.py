"""New tabs in the desktop window - the two ways they silently die (#p1-links).

The desktop shell shows the app in a WebView2 window pointed at
http://127.0.0.1:<port>, which Tauri treats as a REMOTE origin. Two things
about that window are invisible from a browser:

1. ``tauri-plugin-opener``'s injected script cancels every click on a link
   that opens a new tab (``target="_blank"``) and asks the shell to open it
   in the system browser through ``plugin:opener|open_url``. A capability
   file covers only the shell's own local pages unless it names the served
   origins, and the only file was ``default.json`` (the splash's), so the
   request was refused - silently, after the click was already gone. Every
   such link was dead in the desktop app: the external links in the in-app
   docs (``DocMarkdown.tsx``), "Open the still" on the Captures card.
2. ``window.open()`` returns null there: the window has no second window to
   give, so a button built on it does nothing - and no capability fixes it.

None of this can be exercised without the shell (it was proved live over
the WebView2 debug port), so these hold the pieces in place: the grant, its
narrowness, that the splash's own capability gained nothing from the fix,
and that no UI code relies on ``window.open()``.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPS = os.path.join(ROOT, "desktop", "src-tauri", "capabilities")
UI_SRC = os.path.join(ROOT, "frontend", "src")

# The origins the shell serves the app on - the same two ``capture::pin_capability``
# grants the capture commands to (``capture_origins``), with the port a wildcard
# because the shell picks a free one at launch. An omitted port in a Tauri remote
# pattern means the scheme's default port only, which the shell never uses.
SERVED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*"]

# What the served pages may ask of the shell: open_url, and the plugin's
# default URL scope (http, https, mailto, tel). Nothing else - not open_path,
# not the shell's commands, not the capture commands.
SERVED_PERMISSIONS = ["opener:allow-default-urls", "opener:allow-open-url"]

# The splash's capability as it was before the fix: the shell's nine commands
# (one per entry of build.rs's manifest for the splash), Tauri's core default,
# the opener's default and open_url, and open_path scoped to the resource and
# the data folder. The fix adds nothing here: a remote grant in this file, or
# a capture command, would hand the splash's powers to the served pages.
SPLASH_PERMISSIONS = [
    "allow-diagnostics",
    "allow-env-report",
    "allow-open-state-dir",
    "allow-restart-server",
    "allow-save-report",
    "allow-server-alive",
    "allow-server-log",
    "allow-server-ready",
    "allow-server-url",
    "core:default",
    "opener:allow-open-path",
    "opener:allow-open-url",
    "opener:default",
]


def _capability(name):
    with open(os.path.join(CAPS, name), encoding="utf-8") as f:
        return json.load(f)


def _identifiers(permissions):
    """Each permission's identifier, whether it is a bare string or a scoped
    object (``{"identifier": ..., "allow": [...]}``)."""
    return sorted(p["identifier"] if isinstance(p, dict) else p for p in permissions)


def test_the_served_pages_may_open_links_and_nothing_else():
    cap = _capability("served-app.json")
    assert cap["identifier"] == "served-app"
    assert cap["windows"] == ["main"]
    # the served pages only: the splash (a local tauri:// page) has its own grant
    assert cap.get("local") is False
    assert cap["remote"]["urls"] == SERVED_ORIGINS
    assert _identifiers(cap["permissions"]) == SERVED_PERMISSIONS


def test_the_splash_capability_gained_nothing():
    """``default.json`` is the splash's: no ``remote`` key (which would hand
    the shell's commands to the served pages) and exactly the permissions it
    had before the fix - a capture command added here would reach a page
    the shell never started the backend for."""
    cap = _capability("default.json")
    assert "remote" not in cap, "default.json grants remote pages access"
    assert cap.get("local", True) is True
    assert _identifiers(cap["permissions"]) == SPLASH_PERMISSIONS


def test_no_other_capability_reaches_the_served_pages():
    """The capture commands are granted at run time to the backend's own port
    (``capture::pin_capability``), never by a file: a second file with a
    ``remote`` key would widen that to every local port."""
    files = sorted(name for name in os.listdir(CAPS) if name.endswith(".json"))
    assert files == ["default.json", "served-app.json"], files
    for name in files:
        if name == "served-app.json":
            continue
        assert "remote" not in _capability(name), f"{name} grants remote pages access"


def _window_open_users():
    found = []
    for base, _dirs, files in os.walk(UI_SRC):
        for name in files:
            if not name.endswith((".js", ".jsx", ".ts", ".tsx")):
                continue
            path = os.path.join(base, name)
            with open(path, encoding="utf-8") as f:
                if "window.open(" in f.read():
                    found.append(os.path.relpath(path, UI_SRC).replace(os.sep, "/"))
    return found


def test_no_ui_code_relies_on_a_second_window():
    """A link with target="_blank" works in both the browser and the desktop
    app (through the grant above); ``window.open()`` works in the browser
    only, and the grant does nothing for it."""
    assert not _window_open_users(), "window.open() returns null in the desktop app - use a link that opens a new tab"
