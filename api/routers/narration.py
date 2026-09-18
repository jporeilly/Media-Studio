"""The narration editor: per-sentence adjustments to a transcribed video.

Six routes:

- ``PATCH /api/projects/{pid}/transcript/{index}`` nudges when a single
  sentence is spoken, mutes it, or gives it its own voice or speed;
- ``PATCH /api/projects/{pid}/narration/offsets`` nudges SEVERAL sentences at
  once - the timeline's drag - as one write;
- ``GET /api/projects/{pid}/transcript/{index}/preview`` speaks that one
  sentence back, so a voice or a speed can be heard before a whole re-voice is
  run for it;
- ``GET /api/projects/{pid}/narration/plan`` says what every sentence will be
  spoken as, how fast and in whose voice, so the timeline can audition the
  whole narration without rendering anything;
- ``GET /api/projects/{pid}/transcript/download`` hands the transcript over as
  a file - SRT, TXT or JSON - timed as the re-voice will speak it or as it was
  spoken in the source;
- ``GET /api/projects/{pid}/waveform`` returns the peaks of the extracted
  audio, which the timeline strip is drawn from.

All are thin over ``services.narration`` (which owns the locking, the
validation and the synthesis): the routes check the project exists and is the
caller's and that it is a video, and map the service's errors to HTTP answers.
The two PATCHes additionally refuse a write while a job is attached to the
project (``services.jobs.require_idle``, a 409 - every slide write takes it and
these must too, since a re-voice job reads the transcript it is adjusting). The
four GETs do NOT: they write nothing to the project, and refusing to let
someone listen to, look at or download it while a job runs would be a 409 on a
read.

``SegmentNotFound`` is answered differently by the two on purpose, and both are
right: the PATCH is a write whose index argument is out of range (400, as it has
answered since phase 1), the preview is a GET of something that is not there
(404).

Its own router rather than another route on ``api.routers.projects``, following
``api.routers.slides``: the transcript editor is a feature area, and the
waveform route of phase 3 belongs beside these rather than in the projects
module.

The WHOLE-LIST ``PATCH /{pid}/transcript`` stays where it is
(``api.routers.projects``) and stays a text editor. One writer per concern.
"""

import unicodedata
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from api.audit import PROJECT_TRANSCRIPT_TIMING, audit
from api.deps import current_user, readable_project, writable_project
from api.routers.projects import MUTABLE_MEDIA_HEADERS
from api.schemas import OffsetsIn, SegmentOverride
from services import narration, waveform
from utils.helpers import sanitize_filename

router = APIRouter(prefix="/projects", tags=["narration"])

NOT_A_VIDEO = "Only video projects have a transcript."


def readable(pid: str, user: dict) -> dict:
    """A video project this user may see (``api.deps.readable_project``):
    404 missing, 403 someone else's, 400 a deck or PDF."""
    return readable_project(pid, user, narration.NARRATION_KINDS, NOT_A_VIDEO)


def writable(pid: str, user: dict) -> dict:
    """``readable`` plus 409 while a job holds the project."""
    return writable_project(pid, user, narration.NARRATION_KINDS, NOT_A_VIDEO)


@router.patch("/{pid}/transcript/{index}")
def update_transcript_segment(
    pid: str, index: int, body: SegmentOverride, user: dict = Depends(current_user),
):
    """Adjust one transcript sentence: its offset, whether it is muted, and its
    own voice / speed. A field left out is left alone; an explicit null clears
    it. Returns the sentence as it now stands."""
    writable(pid, user)
    # Only what the request carried: a field left out stays as it is, an
    # explicit null clears the override.
    changes = body.model_dump(exclude_unset=True)
    provider = changes.pop("provider", None)
    try:
        segment = narration.update_segment(pid, index, provider=provider, **changes)
    except narration.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (narration.SegmentNotFound, ValueError) as exc:
        # An index past the end is a bad argument to a write, not a missing
        # page: 400 with the range named, as phase 1 shipped it.
        raise HTTPException(status_code=400, detail=str(exc))
    # Field names only, never the sentence - the same rule the settings audits
    # follow (api/audit.py). This is the route that decides where a sentence
    # lands in the narration, so "who moved this" has to be answerable.
    audit(PROJECT_TRANSCRIPT_TIMING, user=user, entity="project", entity_id=pid,
          detail=f"segment {index}: {', '.join(sorted(changes)) or '(nothing)'}")
    return segment


@router.patch("/{pid}/narration/offsets")
def update_narration_offsets(pid: str, body: OffsetsIn, user: dict = Depends(current_user)):
    """Move several sentences at once: the timeline's drag, its nudge keys and
    its Reset timing. One request for however many blocks moved, applied as
    ONE write under the project's lock (``services.narration.update_offsets``),
    so a marquee of twelve sentences is never twelve interleaving writes. The
    single-sentence PATCH above is unchanged and stays the List view's; the
    stored offsets are the same keys either way.

    Answers: 200 ``{"sentences": [...]}`` - the updated sentences, each with
    its ``index`` in the stored transcript -, 400 a deck/PDF, an index outside
    the transcript (named) or given twice, or a bad offset, 404 no such
    project, 409 a job holds the project, 422 an unknown key, an empty list, a
    non-integer index or an offset that is not a number or is out of range.
    """
    writable(pid, user)
    try:
        updated = narration.update_offsets(pid, [(entry.index, entry.offset) for entry in body.offsets])
    except narration.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (narration.SegmentNotFound, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Once for the batch, and the indices and the field name only - never the
    # values, which would say where every sentence was moved to, and never
    # the words - as the single-sentence route above records its one.
    audit(PROJECT_TRANSCRIPT_TIMING, user=user, entity="project", entity_id=pid,
          detail=f"segments {', '.join(str(sentence['index']) for sentence in updated)}: offset")
    return {"sentences": updated}


@router.get("/{pid}/transcript/{index}/preview")
def preview_transcript_segment(
    pid: str, index: int,
    provider: str | None = Query(None, description="edge_tts | kokoro; omitted = the studio's"),
    voice: str | None = Query(None, max_length=narration.MAX_VOICE_CHARS,
                              description="the job's voice; the sentence's own still wins"),
    speed: float | None = Query(None, ge=narration.MIN_SPEED, le=narration.MAX_SPEED,
                                description="the job's speed; the sentence's own still wins"),
    user: dict = Depends(current_user),
):
    """Hear one transcript sentence, spoken as the render would speak it.

    The words are the STORED ones - the transcript editor has an explicit Save,
    so you can only hear what is saved, which is what lets this be a GET (the
    browser caches it, and it stays out of the audit guard honestly: nothing is
    written). The three query parameters are the JOB's narration - what the
    Re-voice card has selected - and the sentence's own voice and speed still
    win over them, exactly as they do at render time. With none of them, the
    sentence's own overrides are used, then the studio defaults.

    It is deliberately NOT a job: ``services.jobs`` allows one job per project,
    so a preview-as-job would block the re-voice this is auditioning for. The
    first press costs a provider round trip (Edge: about a second warm, ~10 s
    for the first one after the server starts); the second is a file copy,
    because the synthesis cache is keyed on (text, voice, speed) and the render
    reuses the very same entry.

    Answers: 200 the audio, 400 a deck/PDF or an unknown provider or a voice
    from the other provider, 404 no such project / no transcript / no such
    sentence, 409 Kokoro's model is not downloaded yet, 502 the provider
    returned nothing (or nothing in time).
    """
    readable(pid, user)
    try:
        path = narration.preview_segment(pid, index, provider=provider, voice=voice, speed=speed)
    except (narration.ProjectNotFound, narration.SegmentNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except narration.KokoroModelMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except narration.PreviewUnavailable as exc:
        # Never a 500: the provider failing is an upstream failure, and the
        # message says which provider and what to try.
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Both providers write mp3 (``utils.helpers.get_cache_path``'s default
    # extension); served inline so a plain <audio src="…"> plays it.
    return FileResponse(str(path), media_type="audio/mpeg")


@router.get("/{pid}/narration/plan")
def get_narration_plan(
    pid: str,
    provider: str | None = Query(None, description="edge_tts | kokoro; omitted = the studio's"),
    voice: str | None = Query(None, max_length=narration.MAX_VOICE_CHARS,
                              description="the job's voice; each sentence's own still wins"),
    speed: float | None = Query(None, ge=narration.MIN_SPEED, le=narration.MAX_SPEED,
                                description="the job's speed; each sentence's own still wins"),
    user: dict = Depends(current_user),
):
    """Everything the timeline needs to audition a re-voice without running one.

    **The server owns what each sentence says and how fast; the client owns only
    when each clip lands.** This is the architectural point of the route: the
    render's rate rules (the per-sentence fitting over a sentence's window, the
    measured TTS baseline, the explicit-speed bypass) are answered here from the
    very functions ``_revoice_video`` calls, rather than being written a second
    time in TypeScript where they would be free to drift. What is left to the
    browser is the one piece only it can do - where each clip actually lands,
    which needs the real decoded length of each clip.

    Every sentence comes back with the EFFECTIVE voice and speed the render will
    synthesise it with, and a ``preview_url`` carrying them, so the clips the
    page fetches are byte-identical to the ones the re-voice will reuse from the
    shared cache.

    The three query parameters are the JOB's narration - what the Re-voice card
    has selected - with the same shape and precedence as the preview route: any
    of them may be omitted for the studio default, and each sentence's own
    voice and speed still win over them.

    Like the preview, it writes nothing and so does NOT take
    ``jobs.require_idle``: refusing an audition while a re-voice runs would be a
    409 on a read.

    Answers: 200 the plan, 400 a deck/PDF or an unknown provider or a voice from
    the other provider, 404 no such project or no transcript yet.
    """
    readable(pid, user)
    try:
        return narration.plan(pid, provider=provider, voice=voice, speed=speed)
    except (narration.ProjectNotFound, narration.SegmentNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _printable(text: str) -> str:
    """``text`` with the control characters dropped (U+0000-U+001F, U+007F).

    A header cannot carry them - h11 refuses the response outright and
    uvicorn drops the connection - and ``sanitize_filename`` does not strip
    them, so a project name with a newline in it (a hand-edited record; an
    upload cannot make one on Windows) would turn a download into a closed
    socket. Applied to both forms of the name below.
    """
    return "".join(ch for ch in text if ord(ch) >= 0x20 and ord(ch) != 0x7F)


def _attachment(filename: str) -> str:
    """The ``Content-Disposition`` of a download built from bytes - what
    ``FileResponse(filename=...)`` writes for a file on disk, which the other
    download routes lean on and this one cannot.

    A plain quoted name while it is ASCII; otherwise RFC 5987's ``filename*``
    beside an ASCII fallback, because a header is Latin-1 and a project called
    "Présentation" would otherwise be a 500. The fallback is the NFKD fold of
    the name (é -> e) put through ``utils.helpers.sanitize_filename`` AGAIN
    afterwards: the fold turns fullwidth punctuation into the ASCII quote,
    backslash and slash that the first sanitising never saw, and an unescaped
    quote inside a quoted-string is not a header at all. A stem that folds
    away to nothing (a wholly non-Latin name) is called ``transcript`` rather
    than leaving a legacy client to save ``-narration.srt``. The ASCII form
    therefore carries no quote or backslash, so the quoted form needs no
    escaping.
    """
    filename = _printable(filename)
    if filename.isascii():
        return f'attachment; filename="{filename}"'
    folded = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    fallback = sanitize_filename(_printable(folded))
    if fallback.startswith(f"{narration.EXPORT_SUFFIX}."):
        fallback = "transcript" + fallback
    return f"attachment; filename=\"{fallback}\"; filename*=utf-8''{quote(filename)}"


@router.get("/{pid}/transcript/download")
def download_transcript(
    pid: str,
    # The literals mirror ``narration.EXPORT_FORMATS`` / ``EXPORT_VIEWS``; an
    # unknown value is a 422 from the schema, before the endpoint runs.
    fmt: Literal["srt", "txt", "json"] = Query("srt", alias="format", description="srt | txt | json"),
    view: Literal["timeline", "source"] = Query(
        "timeline",
        description="timeline: as the re-voice will speak it; source: as spoken in the source video",
    ),
    user: dict = Depends(current_user),
):
    """Download the transcript as a file.

    Two timing views, because the two questions are different: ``timeline``
    is the narration as the re-voice will speak it - projected through the
    edit, muted and dropped sentences left out, each sentence at the moment
    it is AIMED at - and ``source`` is the script as it was spoken in the
    original recording, every sentence at Whisper's own time, muted ones
    marked. The timeline's numbers are the audition plan's own
    (``services.narration.project_narration``), never a second projection.

    A read: it writes nothing, so like the preview and the plan it does NOT
    take ``jobs.require_idle`` (a download while a re-voice runs is fine) and
    is not audited (``tests/test_audit.py`` counts every non-GET as mutating).
    ``Cache-Control: no-cache`` because the transcript is rewritten in place -
    the same header the re-voiced video and the tracks carry, for the same
    reason (``MUTABLE_MEDIA_HEADERS``).

    Answers: 200 the file, named ``<project name>-narration.<ext>``, 400 a
    deck/PDF or an edit that cannot be read or measured, 404 no such project
    or no transcript yet, 422 an unknown format or view.
    """
    readable(pid, user)
    try:
        filename, media_type, body = narration.export_transcript(pid, fmt, view)
    except (narration.ProjectNotFound, narration.SegmentNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(
        content=body, media_type=media_type,
        headers={**MUTABLE_MEDIA_HEADERS, "Content-Disposition": _attachment(filename)},
    )


@router.get("/{pid}/waveform")
def get_waveform(pid: str, user: dict = Depends(current_user)):
    """The peaks of the project's extracted audio, for the timeline strip.

    One magnitude (0-255) per 125 ms bucket, taken from the project's own
    ``audio.wav`` with the stdlib ``wave`` module - no ffmpeg and no ffprobe,
    neither of which can be relied on in the packaged app. Computed once and
    cached beside the audio, keyed on that file's mtime and size, so a
    re-transcription invalidates it by itself.

    The ``duration`` returned is the WAV header's, not the record's: the strip
    and the sentence blocks drawn over it must share one scale, and it has to
    be the scale of the file being drawn.

    A plain ``def``: FastAPI runs sync endpoints in its threadpool, every other
    route in this module is sync, and the read is CPU work rather than IO to
    await.

    Answers: 200 the peaks, 400 a deck or PDF, 404 no such project or the video
    has not been transcribed yet, 422 the audio is not PCM we can read.
    """
    readable(pid, user)
    try:
        return waveform.peaks_for(pid)
    except waveform.NoAudio as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except waveform.UnreadableAudio as exc:
        # Not a 500: the file is there and we simply cannot draw it. The
        # timeline falls back to blocks with no waveform behind them.
        raise HTTPException(status_code=422, detail=str(exc))
