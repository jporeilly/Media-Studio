"""Screen capture (T3): the backend's half.

The desktop shell records the screen in the page (``getDisplayMedia``: the
only route to system sound without a driver) and takes STILLS here, with the
bundled ffmpeg's ``gdigrab``, which copies the screen pixel for pixel (a frame
of the display stream passes through 4:2:0 and comes back with faint colour
fringes on text - measured: a third of the pixels off by 1-2 and some edges
by up to 32, against gdigrab's exact copy) and draws the pointer on request
(``-draw_mouse``). A region is a physical-pixel rectangle in virtual-screen
coordinates (negative offsets are legal: a monitor left of or above the
primary), exactly as the shell's overlay reports it.

Only meaningful on the machine the screen belongs to - the desktop edition,
where the backend and the screen are one machine. The UI hides the feature
elsewhere, and every route that touches the host's screen refuses a client
that is not on this computer (``api.routers.capture.screen_user``).
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from utils.config import FFMPEG_PATH
from utils.logger import get_logger

log = get_logger("CAPTURE")

#: A still is one frame; the grab itself takes well under a second, and a
#: machine that cannot do it in this long has a problem the user should hear
#: about rather than wait on.
STILL_TIMEOUT_SECONDS = 20
#: Bounds on a region, physical pixels (the virtual screen can be wide:
#: three 4K monitors side by side are 11,520 across).
MAX_DIMENSION = 32_000
MIN_DIMENSION = 1

FFMPEG_MISSING = "ffmpeg is not available, so the screen cannot be captured."


class CaptureError(RuntimeError):
    """A still could not be taken; the message is for the user."""


def validate_region(x: int, y: int, width: int, height: int) -> None:
    """Refuse a rectangle that is empty or absurd. Offsets may be negative."""
    if not (MIN_DIMENSION <= width <= MAX_DIMENSION and MIN_DIMENSION <= height <= MAX_DIMENSION):
        raise ValueError(f"The capture size must be {MIN_DIMENSION}-{MAX_DIMENSION} pixels a side.")
    if not (-MAX_DIMENSION <= x <= MAX_DIMENSION and -MAX_DIMENSION <= y <= MAX_DIMENSION):
        raise ValueError("The capture offset is out of range.")


def still_command(ffmpeg: str, x: int, y: int, width: int, height: int, cursor: bool, out: Path) -> list[str]:
    """The gdigrab argv for one PNG frame of the region. ``-framerate 2``
    keeps gdigrab's own pacing short (it sleeps a frame period before the
    first grab); ``-draw_mouse`` composites the pointer when asked."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "gdigrab", "-framerate", "2", "-draw_mouse", "1" if cursor else "0",
        "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{width}x{height}",
        "-i", "desktop", "-frames:v", "1", str(out),
    ]


def _grab(x: int, y: int, width: int, height: int, *, cursor: bool, out: Path) -> Path:
    """Run gdigrab for one frame of the region into ``out``; the shared body
    of :func:`take_still` and :func:`freeze`."""
    validate_region(x, y, width, height)
    if not FFMPEG_PATH:
        raise CaptureError(FFMPEG_MISSING)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = still_command(FFMPEG_PATH, x, y, width, height, cursor, out)
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
            timeout=STILL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureError("The screen grab did not finish in time.") from exc
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        reason = (proc.stderr or "").strip().splitlines()
        log.error("gdigrab failed (%s): %s", proc.returncode, reason[-1] if reason else "no output")
        out.unlink(missing_ok=True)
        raise CaptureError("The screen could not be captured: " + (reason[-1] if reason else "ffmpeg gave no reason."))
    return out


def take_still(x: int, y: int, width: int, height: int, *, cursor: bool, out_dir: Path) -> Path:
    """Grab the region into ``out_dir/still-<id>.png`` and return the path.
    Raises ``ValueError`` for a bad rectangle and ``CaptureError`` when ffmpeg
    is missing, fails or produces nothing."""
    return _grab(x, y, width, height, cursor=cursor, out=out_dir / f"still-{uuid.uuid4().hex[:12]}.png")


#: The frozen frames the region overlay draws over: one per monitor, named
#: by the monitor's index. One set at a time - a freeze replaces the last.
FROZEN_NAME = "frozen-{index}.png"
#: More monitors than any desk has; a bound on a body that names them.
MAX_MONITORS = 16


def freeze(monitors: list[dict], out_dir: Path) -> list[int]:
    """Photograph every monitor BEFORE the overlay windows open - opened
    first, each overlay would photograph itself (measured: a black frame).
    ``monitors`` carry ``index, x, y, width, height`` as the shell reports
    them; the previous set is removed first. Returns the indexes frozen.
    The pointer is left out: on the overlay the crosshair is the pointer."""
    if not 1 <= len(monitors) <= MAX_MONITORS:
        raise ValueError(f"Between 1 and {MAX_MONITORS} monitors can be frozen.")
    out_dir.mkdir(parents=True, exist_ok=True)
    clear_frozen(out_dir)
    done: list[int] = []
    for m in monitors:
        index = int(m["index"])
        _grab(int(m["x"]), int(m["y"]), int(m["width"]), int(m["height"]), cursor=False,
              out=out_dir / FROZEN_NAME.format(index=index))
        done.append(index)
    return done


def frozen_path(out_dir: Path, index: int) -> Path | None:
    """The frozen frame of monitor ``index``, or None when no freeze holds one."""
    path = out_dir / FROZEN_NAME.format(index=int(index))
    return path if path.is_file() else None


def clear_frozen(out_dir: Path) -> int:
    """Delete the frozen set - full-resolution pictures of every monitor - as soon as it has served (the
    overlay is done, the shared screen is matched) and at every startup: nothing of the desktop is kept
    that nobody asked to keep. Returns how many frames went.

    Best-effort, frame by frame: one that cannot be deleted - held open by a viewer, a scanner or the
    indexer (Windows refuses the delete while a handle without FILE_SHARE_DELETE is open) - is logged and
    left for the next clear, the others still go, and nothing is raised: the app's startup calls this,
    and a picture nobody can delete must never stop the app from starting."""
    gone = 0
    try:
        frames = list(out_dir.glob(FROZEN_NAME.format(index="*"))) if out_dir.is_dir() else []
    except OSError as exc:
        log.warning("Could not list the frozen frames in %s: %s", out_dir, exc)
        return 0
    for old in frames:
        try:
            old.unlink(missing_ok=True)
            gone += 1
        except OSError as exc:
            log.warning("Could not delete the frozen frame %s (left for the next clear): %s", old.name, exc)
    return gone
