"""The edit: which ranges of a video's one source are kept, per track, the
music clips placed on the output, and the markers - named moments of the
picture's source (porting vertical 6: E1 the model and the render, E2 the
timeline gesture, E3 one list per track - video and narration -, E4 the
music lane, E5b the markers).

Three routes, thin over ``services.edit``, which owns the validation, the
projection and the locking:

- ``GET /api/projects/{pid}/edit`` - each track's kept ranges (``null`` means
  everything), the music clips, the markers with where each lands, the
  source's length and the output's;
- ``PUT /api/projects/{pid}/edit`` - set the keys the body names, leave the
  ones it does not (one rule for the two tracks, the music and the markers
  alike);
- ``DELETE /api/projects/{pid}/edit`` - back to keep-everything, no music,
  no markers.

The GET is a read and is allowed while a job holds the project. The PUT and
the DELETE write the record, so they take ``jobs.require_idle`` (a 409) like
every other write of it: a re-voice job reads the edit it is rendering.

There is no render route of its own. The render IS the re-voice job with the
projection applied (``services.revoice``) - one job kind, one code path - so
``POST /api/projects/{pid}/revoice`` starts it, exactly as before.

Its own router, following ``api.routers.narration``: the edit is a feature
area of its own, and the projects module is a store of source files.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.audit import PROJECT_EDIT, audit
from api.deps import current_user, readable_project, writable_project
from api.schemas import EditIn
from services import edit, narration

router = APIRouter(prefix="/projects", tags=["edit"])

NOT_A_VIDEO = "Only video projects can be cut."


def readable(pid: str, user: dict) -> dict:
    """A video project this user may see (``api.deps.readable_project``):
    404 missing, 403 someone else's, 400 a deck or PDF."""
    return readable_project(pid, user, narration.NARRATION_KINDS, NOT_A_VIDEO)


def writable(pid: str, user: dict) -> dict:
    """``readable`` plus 409 while a job holds the project."""
    return writable_project(pid, user, narration.NARRATION_KINDS, NOT_A_VIDEO)


def _summary(stored: dict) -> str:
    """The audit detail, per track: how many ranges and how much was removed,
    or "whole" - never the times, which would say where every cut is -,
    when clips are stored, how many, never their files or positions, and,
    when markers are stored, how many, never their names or moments."""
    parts = []
    for track in edit.TRACKS:
        held = stored[track]
        if held["keep"] is None:
            parts.append(f"{track}: whole")
            continue
        count = len(held["keep"])
        removed = max(0.0, (stored["source_duration"] or 0.0) - held["output_duration"])
        parts.append(f"{track}: {count} range{'' if count == 1 else 's'} kept, {removed:.1f} s removed")
    clips = len(stored.get("music") or [])
    if clips:
        parts.append(f"music: {clips} clip{'' if clips == 1 else 's'}")
    markers = len(stored.get("markers") or [])
    if markers:
        parts.append(f"markers: {markers}")
    return "; ".join(parts)


@router.get("/{pid}/edit")
def get_edit(pid: str, user: dict = Depends(current_user)):
    """The project's edit: for each of ``video`` and ``narration``, ``keep``
    is the list of kept ``[start, end]`` ranges in source seconds, or
    ``null`` when that track keeps the whole source, with that track's
    ``output_duration``; ``music`` the clips, each with its file's
    ``file_duration`` and ``missing`` (the file has left the library - the
    render will refuse until the clip is removed or the file uploaded
    again); ``markers`` the named moments of the picture's source, each with
    its stored ``at`` in source seconds and ``timeline_at``, where it lands
    in the output through the video list (``null`` for one in removed
    picture); ``source_duration`` is the extracted audio's length (``null``
    until the video has been transcribed) and the top-level
    ``output_duration`` what a render would be - the picture's - (``null``
    while neither is known). A version-1 edit answers in this shape, both
    tracks alike and no markers.

    Answers: 200, 400 a deck/PDF or an edit this version cannot read, 404 no
    such project.
    """
    record = readable(pid, user)
    try:
        return edit.describe(record)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/{pid}/edit")
def put_edit(pid: str, body: EditIn, user: dict = Depends(current_user)):
    """Replace the edit. **One rule for every key**: a key the body does not
    name is left exactly as it was stored, ``null`` clears it - a track back
    to whole, the music gone, the markers gone - and a list replaces it.
    ``video`` and ``narration`` take ordered, non-overlapping ranges within
    the source in source seconds; ``music`` takes the lane's clips (``[]``
    clears them too); ``markers`` takes the named moments of the picture's
    source, ``at`` in source seconds within it (``[]`` clears them too);
    ``keep`` is the version-1 body and means BOTH tracks. Returns the edit
    as stored.

    So a cut sends its track lists and no ``music`` or ``markers`` and never
    drops either, a clip commit sends ``music`` and no tracks and never
    drops the picture's cut, and a marker commit sends ``markers`` alone. A
    body naming nothing at all (``{}``) is a 200 that changes nothing and
    records nothing, like a ``DELETE`` with nothing to clear.

    Answers: 200 the stored edit, 400 a bad list (the message names the track
    and the range, the clip and the field - a clip naming a file that is not
    in the library included -, or the marker and the field), ``keep`` beside
    a per-track list, ``music`` or ``markers``, a stored edit this version
    cannot read, or a deck/PDF, 404 no such project, 409 a job holds the
    project or a track list or a marker list was sent for a video with no
    extracted audio yet (both are checked against its length, and
    transcribing is what extracts it).
    """
    writable(pid, user)
    named = body.model_fields_set
    if body.keep is not None and named & {"video", "narration", "music", "markers"}:
        raise HTTPException(status_code=400, detail="Send either keep (both tracks) or video / narration / music / markers, not both.")
    # A key the body did not name is UNCHANGED; one it named as null is a
    # clear. ``keep`` names both tracks at once, for curl and E1's shape.
    if body.keep is not None:
        video = narration_keep = body.keep
    else:
        video = body.video if "video" in named else edit.UNCHANGED
        narration_keep = body.narration if "narration" in named else edit.UNCHANGED
    music = edit.UNCHANGED
    if "music" in named:
        music = None if body.music is None else [clip.model_dump(by_alias=True) for clip in body.music]
    markers = edit.UNCHANGED
    if "markers" in named:
        markers = None if body.markers is None else [marker.model_dump() for marker in body.markers]
    try:
        stored, changed = edit.set_edit(pid, video=video, narration=narration_keep, music=music, markers=markers)
    except edit.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except edit.SourceLengthUnknown as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if changed:
        audit(PROJECT_EDIT, user=user, entity="project", entity_id=pid, detail=_summary(stored))
    return stored


@router.delete("/{pid}/edit")
def delete_edit(pid: str, user: dict = Depends(current_user)):
    """Back to keep-everything: the tracks whole, the music gone and the
    markers gone. Returns the edit as it now stands (both tracks' ``keep``
    null, ``music`` and ``markers`` empty). Idempotent, and a project that
    had no edit is left as it is and records nothing - there was nothing to
    clear. Answers: 200, 400 a deck/PDF, 404 no such project, 409 busy."""
    writable(pid, user)
    try:
        cleared, had_edit = edit.clear_edit(pid)
    except edit.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if had_edit:
        audit(PROJECT_EDIT, user=user, entity="project", entity_id=pid, detail="cleared")
    return cleared
