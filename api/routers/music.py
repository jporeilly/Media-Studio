"""The music library (porting vertical 6, phase E4a): the files a project's
music clips refer to by name, studio-wide.

Five routes, thin over ``services.music``, which owns the names, the index,
the decode and the reference check:

- ``GET /api/music`` - the index, sorted by name;
- ``POST /api/music`` - a multipart upload, decoded once to prove it is audio
  and measure it (400 not audio, 409 the name exists, 413 too big);
- ``GET /api/music/{name}`` - the file, for the audition;
- ``GET /api/music/{name}/peaks`` - the cached peaks, for the lane;
- ``DELETE /api/music/{name}`` - refused with a 409 naming the projects
  while any project's edit still names the file.

Every route needs a session and none is project-scoped: the library is shared
by every account like the voices and the studio settings (spec §12.3), so the
routes carry no ``{pid}`` and are not in the ownership sweep's table - they are
in its exclusion list (``tests/test_project_ownership.py``), with the reason.

``name`` is a path parameter checked against ``services.music.NAME_PATTERN``
(no separator either way round, no control character; 422 otherwise), and the
service resolves the path and confirms it sits inside the library directory
before it is ever read, so a crafted name cannot reach a file outside it.
The name is matched EXACTLY: an upload of a name that differs from a stored
one only by case is refused (409), and a GET, a peaks read or a DELETE of one
is a 404 - never the other file served or removed.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from api.audit import MUSIC_DELETE, MUSIC_UPLOAD, audit
from api.deps import current_user
from api.store import display_name_of
from services import music

router = APIRouter(prefix="/music", tags=["music"])

# A library file never changes under its name (an upload of an existing name
# is refused), so a day of private caching is right - unlike the re-voiced
# outputs, which are rewritten in place and served ``no-cache``.
IMMUTABLE_MEDIA_HEADERS = {"Cache-Control": "private, max-age=86400"}

Name = Annotated[str, PathParam(pattern=music.NAME_PATTERN)]


@router.get("")
def list_music(user: dict = Depends(current_user)):
    """The library: ``files``, each with ``name``, ``size``, ``duration``,
    ``sample_rate``, ``channels``, ``uploaded_at`` and ``uploaded_by``,
    sorted by name."""
    return {"files": music.list_files()}


@router.post("")
def upload_music(file: UploadFile = File(...), user: dict = Depends(current_user)):
    """Add a file to the library: multipart ``file``, accepted by extension
    (mp3, wav, m4a, aac, ogg, flac), decoded once to prove it is audio and to
    measure it, its peaks cached. Returns the new index entry.

    A plain ``def`` rather than the ``import_project`` route's ``async``: the
    decode takes seconds for a long file and must not hold the event loop, so
    FastAPI runs this on its thread pool and the body is read from the
    spooled upload directly.

    Answers: 200 the entry, 400 not audio, wider than stereo, or a name the
    library cannot store,
    409 a file of that name is already there - matched case-insensitively, so
    ``BED.mp3`` beside ``bed.mp3`` is refused on every platform (rename it,
    or delete the old one: a clip refers to a file by name, so nothing is
    ever replaced) -, 413 over the size limit.
    """
    limit = music.MAX_MUSIC_BYTES
    too_big = f"A music file is limited to {limit // (1024 * 1024)} MB."
    # Refused from the metadata first, then from what is really there: one
    # byte past the cap is read so an oversize body is never buffered whole.
    if file.size is not None and file.size > limit:
        raise HTTPException(status_code=413, detail=too_big)
    data = file.file.read(limit + 1)
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=too_big)
    try:
        entry = music.add_file(file.filename or "", data, display_name_of(user))
    except music.TooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except music.MusicExists as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (music.BadName, music.NotAudio, music.TooManyChannels, music.MusicNotFound) as exc:
        # ``MusicNotFound`` from an upload means the name would not resolve
        # inside the library directory - a 400 about the name, never a 500.
        raise HTTPException(status_code=400, detail=str(exc))
    audit(MUSIC_UPLOAD, user=user, entity="music", entity_id=entry["name"],
          detail=f"{entry['name']} ({entry['size']} bytes)")
    return entry


@router.get("/{name}")
def get_music(name: Name, user: dict = Depends(current_user)):
    """The file itself, served inline with its media type for the audition's
    ``decodeAudioData``. 404 when the name is not in the library."""
    try:
        path = music.get_path(name)
    except music.MusicNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(str(path), media_type=music.media_type_of(name), headers=IMMUTABLE_MEDIA_HEADERS)


@router.get("/{name}/peaks")
def get_music_peaks(name: Name, user: dict = Depends(current_user)):
    """The file's peaks in the waveform route's shape (``buckets``,
    ``bucket_seconds``, ``duration``, ``sample_rate``, ``peaks``), cached at
    upload. 404 when the name is not in the library; 422 when the file has
    gone unreadable since (the cache was removed and the file no longer
    decodes)."""
    try:
        return music.peaks(name)
    except music.MusicNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except music.NotAudio as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/{name}", status_code=204)
def delete_music(name: Name, user: dict = Depends(current_user)):
    """Remove a file, its peaks and its index row. 404 when there is nothing
    of that name; 409 naming the projects while any project's edit still has
    a clip on it - remove the clips first."""
    try:
        music.remove(name)
    except music.MusicNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except music.MusicInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit(MUSIC_DELETE, user=user, entity="music", entity_id=name, detail=name)
