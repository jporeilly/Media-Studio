"""Screen capture (T3): the frozen frames the desktop shell's region overlay
draws over. A still is taken by ``POST /api/captures`` and a recording
arrives through the recordings routes (``api.routers.recordings``); every
route that touches the host's screen depends on ``screen_user``."""

from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.audit import CAPTURE_REFUSED, audit
from api.deps import current_user
from services import capture
from utils.config import TEMP_DIR

router = APIRouter(prefix="/capture", tags=["capture"])

#: What a client anywhere but this computer is told.
LOCAL_ONLY = "Screen capture works only in the desktop app on this computer."
#: Headers a proxy or tunnel adds. The desktop shell's own webview never sends
#: one, and a tunnel or reverse proxy ON this machine (cloudflared, nginx)
#: connects from 127.0.0.1 - so a loopback address that carries one is a remote
#: client, not this computer.
FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip", "cf-connecting-ip", "true-client-ip")


def is_local_client(request: Request) -> bool:
    """Whether the request comes from THIS computer: a loopback client address
    (127.0.0.0/8, ::1, or ::ffff:127.0.0.1, which is how a dual-stack bind
    reports 127.0.0.1) with no proxy header. The desktop shell's window
    loads the backend at 127.0.0.1, so it is local; any other machine - a
    browser on the team server's network - is not."""
    if any(name in request.headers for name in FORWARDING_HEADERS):
        return False
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # An IPv4-mapped loopback (::ffff:127.0.0.1) is loopback to ipaddress
    # itself on both the dev venv (3.13) and the shipped runtime (3.12.8),
    # measured - so no unwrapping here (tests/test_captures.py pins it).
    return ip.is_loopback


def screen_user(request: Request, user: dict = Depends(current_user)) -> dict:
    """The signed-in user, if the request may touch the HOST's screen: every
    route that freezes it, serves or deletes its frozen frames, takes a still,
    or starts or feeds a recording depends on this. Run as a team server (``--host 0.0.0.0``),
    the host's desktop is the SERVER's, and no editor elsewhere may see it -
    so anyone not on this computer gets 403 and the refusal is audited
    (``capture.refused``). The UI hides the feature outside the desktop shell,
    which is always on this computer, so the two rules agree."""
    if not is_local_client(request):
        client = request.client.host if request.client else "unknown"
        audit(CAPTURE_REFUSED, user=user, entity="capture",
              detail=f"{request.method} {request.url.path} from {client}")
        raise HTTPException(status_code=403, detail=LOCAL_ONLY)
    return user

#: Where the frozen frames go: throwaway files under the app's temp directory,
#: deleted once they have served and at every startup (``api.app``).
SCREEN_DIR = TEMP_DIR / "capture"
NO_STORE = {"Cache-Control": "no-store"}


class MonitorIn(BaseModel):
    """A monitor as the shell's ``capture_monitors`` reports it: physical
    virtual-screen pixels, which may be negative."""

    index: int = Field(ge=0, le=capture.MAX_MONITORS)
    x: int
    y: int
    width: int = Field(ge=capture.MIN_DIMENSION, le=capture.MAX_DIMENSION)
    height: int = Field(ge=capture.MIN_DIMENSION, le=capture.MAX_DIMENSION)


class FreezeIn(BaseModel):
    monitors: list[MonitorIn] = Field(min_length=1, max_length=capture.MAX_MONITORS)


def _capture_errors(func, *args, **kwargs):
    """Run a capture call, turning its two refusals into HTTP answers: a bad
    rectangle is the caller's (400), a failed grab is the machine's (503)."""
    try:
        return func(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except capture.CaptureError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/freeze")
def freeze(body: FreezeIn, user: dict = Depends(screen_user)):
    """Photograph the given monitors now, BEFORE the overlay windows open
    (an open overlay would photograph itself), one frame each, replacing the
    previous set. Answers ``{"frozen": [indexes]}``; the frames are served by
    ``GET /api/capture/frozen/{index}``."""
    done = _capture_errors(capture.freeze, [m.model_dump() for m in body.monitors], SCREEN_DIR)
    return {"frozen": done}


@router.delete("/frozen", status_code=204)
def forget_frozen(user: dict = Depends(screen_user)):
    """Delete the frozen set once it has served (the overlay answered, the
    shared screen was matched). Idempotent: nothing to delete is fine."""
    capture.clear_frozen(SCREEN_DIR)


@router.get("/frozen/{index}")
def frozen(index: int, user: dict = Depends(screen_user)):
    """The frozen frame of one monitor, from the last freeze; 404 when there
    is none (no freeze yet, or an index it did not cover)."""
    path = capture.frozen_path(SCREEN_DIR, index)
    if path is None:
        raise HTTPException(status_code=404, detail="No frozen frame for that monitor. Freeze the screen first.")
    return FileResponse(str(path), media_type="image/png", headers=NO_STORE)
