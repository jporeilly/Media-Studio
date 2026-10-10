"""One rule for a large request body: refused with 413 the moment it passes
its cap, read in pieces, so an oversized or lying upload is never held
whole. A recorder's WebM chunk (``api/routers/recordings.py``) and an
annotated still's export (``api/routers/captures.py``) both come through
here, so there is one place to get the rule right."""

from __future__ import annotations

from fastapi import HTTPException, Request


async def capped_body(request: Request, limit: int, too_large: str) -> bytes:
    """The request body, or 413 with ``too_large`` the moment it passes
    ``limit`` - by its declared length first, then as it streams in."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise HTTPException(status_code=413, detail=too_large)
    parts, size = [], 0
    async for piece in request.stream():
        size += len(piece)
        if size > limit:
            raise HTTPException(status_code=413, detail=too_large)
        parts.append(piece)
    return b"".join(parts)
