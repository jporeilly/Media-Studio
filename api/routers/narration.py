"""The narration editor: per-sentence adjustments to a transcribed video.

Five routes:

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
- ``GET /api/projects/{pid}/waveform`` returns the peaks of the extracted
  audio, which the timeline strip is drawn from.

All are thin over ``services.narration`` (which owns the locking, the
validation and the synthesis): the routes check the project exists and is the
caller's and that it is a video, and map the service's errors to HTTP answers.
The two PATCHes additionally refuse a write while a job is attached to the
project (``services.jobs.require_idle``, a 409 - every slide write takes it and
these must too, since a re-voice job reads the transcript it is adjusting). The
three GETs do NOT: they write nothing to the project, and refusing to let
someone listen to or look at it while a job runs would be a 409 on a read.

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

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api.audit import PROJECT_TRANSCRIPT_TIMING, audit
from api.deps import current_user, readable_project, writable_project
from api.schemas import OffsetsIn, SegmentOverride
from services import narration, waveform

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
