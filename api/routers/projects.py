"""Projects: import decks / PDFs / videos, list them, delete them."""

from datetime import datetime, timezone
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from api.audit import (
    PROJECT_DELETE,
    PROJECT_GENERATE,
    PROJECT_IMPORT,
    PROJECT_REVOICE,
    PROJECT_TRANSCRIBE,
    PROJECT_TRANSCRIPT_EDIT,
    audit,
)
from api.deps import current_user, may_access_project, require_project
from api.schemas import GenerateRequest, RevoiceRequest, TranscribeRequest, TranscriptUpdate
from api.store import display_name_of
from services import jobs, narration, projects as store, revoice, slides, studio_settings, transcription, waveform
from services.output_presets import get_preset

router = APIRouter(prefix="/projects", tags=["projects"])

# Guard against unbounded in-memory reads (the upload is read fully before save).
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB

# The sidecar artifacts a generate job records on the project (``outputs``):
# kind -> (media type, served as a download or inline).
OUTPUT_KINDS = {
    "srt": ("application/x-subrip", "attachment"),
    "vtt": ("text/vtt", "attachment"),
    "webm": ("video/webm", "attachment"),
    "gif": ("image/gif", "attachment"),
    "mp3": ("audio/mpeg", "attachment"),
    "preview": ("video/mp4", "inline"),
}


def _narration(provider: str | None, voice_id: str | None) -> tuple[str, str]:
    """The (provider, voice) a narration job runs with, resolved at request time
    so the job is pinned to what the caller asked for (or the studio defaults
    as they are now), whatever an admin changes while it queues."""
    try:
        return studio_settings.resolve_narration(provider, voice_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _media_type_of(path: Path) -> str:
    """The media type for a file served straight from a project directory.

    Guessed from the extension so an imported .mov or .mkv is not served as
    video/mp4, with a plain byte stream when the guess fails rather than a
    claim that could be wrong.
    """
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _project_file(pid: str, filename: str, missing: str) -> Path:
    """A file inside a project's directory, refused if it escapes or is absent.

    Every route that serves a file names it from the project record, so the
    name is only as trustworthy as the record: resolve it, then confirm the
    result is still under the project directory, and a tampered entry can
    never read an arbitrary file. Written once here because four routes need
    it and two of the three hand-written copies this replaced had no test of
    their own.
    """
    base = (store.PROJECTS_DIR / pid).resolve()
    path = (base / filename).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail=missing)
    return path


@router.get("")
def list_projects(user: dict = Depends(current_user)):
    """The caller's projects — every project for an admin, their own otherwise
    (``api.deps.may_access_project`` is the one rule; legacy records with no
    owner are admin-owned)."""
    return {"projects": [p for p in store.list_projects() if may_access_project(p, user)]}


@router.post("/import")
async def import_project(file: UploadFile = File(...), user: dict = Depends(current_user)):
    # Reject an unsupported type or an oversize body up front, from the metadata,
    # before buffering the whole upload into memory.
    if store.kind_for_suffix(Path(file.filename or "").suffix) is None:
        allowed = ", ".join(sorted(store.ALLOWED_SUFFIXES))
        raise HTTPException(status_code=400, detail=f"Unsupported file type. Allowed: {allowed}")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than the 2 GB limit.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:  # fallback when the client sent no size
        raise HTTPException(status_code=413, detail="File is larger than the 2 GB limit.")
    try:
        record = store.import_upload(
            file.filename or "upload", data,
            owner_id=user["id"], owner_name=display_name_of(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(PROJECT_IMPORT, user=user, entity="project", entity_id=record["id"],
          detail=f"{record['kind']}: {record['source_filename']}")
    return record


@router.get("/{pid}")
def get_project(pid: str, user: dict = Depends(current_user)):
    return require_project(pid, user)


# The fields of the project's running job the page needs to follow it: which
# job, which card owns it, whether it is still in flight, and who started it
# (the Cancel rule). Its progress is read from ``GET /api/jobs/{id}``.
ACTIVE_JOB_FIELDS = ("id", "kind", "status", "user_id")


@router.get("/{pid}/job")
def get_active_job(pid: str, user: dict = Depends(current_user)):
    """The job holding this project, whoever started it:
    ``{"active_job": {id, kind, status, user_id}}``, or ``{"active_job": null}``
    when none is queued or running (a finished job is not active).

    A small route of its own rather than a field on the project's GET: the
    page asks it every few seconds while it follows no job, to notice one
    started after a reload, in another tab or by someone else, and the record
    it would otherwise re-read carries the whole transcript. Jobs live in
    memory, so the record on disk is also the wrong place to describe one.
    Guarded like every project route (``require_project``); the job's own
    progress is then read from ``GET /api/jobs/{id}``, which the project's
    owner may read whoever started it.
    """
    require_project(pid, user)
    active = jobs.active_for(pid)
    return {"active_job": {key: active.get(key) for key in ACTIVE_JOB_FIELDS} if active else None}


@router.delete("/{pid}", status_code=204)
def delete_project(pid: str, user: dict = Depends(current_user)):
    """Delete a project and its whole directory. Refused with 409 while a job
    holds the project (``jobs.require_idle``), like every other writer: the
    job has its own copy of the project and would go on writing into a
    directory that is being removed (on Windows a file it holds open made the
    delete fail with "Something is still using it", a message that named no
    job)."""
    record = require_project(pid, user)
    jobs.require_idle(pid)
    try:
        deleted = store.delete_project(pid)
    except store.ProjectDeleteError as exc:
        # Nothing was half-removed: the project is still listed and still whole,
        # so the answer is "try again", not a project that has quietly lost its
        # files. 409 like the other "this project is busy" refusals.
        raise HTTPException(status_code=409, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found.")
    audit(PROJECT_DELETE, user=user, entity="project", entity_id=pid, detail=record.get("name"))


@router.post("/{pid}/transcribe")
def transcribe(pid: str, body: TranscribeRequest | None = None, user: dict = Depends(current_user)):
    """Start transcribing a video project - for the first time, or again over
    the transcript it has (``services.transcription`` says what a second
    transcription replaces, drops and keeps). The same Whisper-model rule
    either way. Returns a job id to poll at /api/jobs/{id}; 409 while another
    job holds the project (``jobs.start``)."""
    record = require_project(pid, user)
    if record.get("kind") != "video":
        raise HTTPException(status_code=400, detail="Only video projects can be transcribed.")
    # The request's model, else the studio's, resolved now so the job is pinned
    # to the setting as it is at request time ("" = the engine's recommended
    # default, chosen when the job runs).
    try:
        model = studio_settings.resolve_whisper_model(body.model if body else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    job_id = jobs.start(
        "transcribe",
        lambda progress: transcription.transcribe_project(pid, model or None, progress),
        project_id=pid, user_id=user["id"],
    )
    audit(PROJECT_TRANSCRIBE, user=user, entity="project", entity_id=pid, detail=f"job {job_id}")
    return {"job_id": job_id}


@router.patch("/{pid}/transcript")
def update_transcript(pid: str, body: TranscriptUpdate, user: dict = Depends(current_user)):
    """Replace the edited transcript segments on a project.

    The words only: ``TranscriptSegment`` forbids the per-sentence timing keys,
    and ``set_transcript`` carries them across by index, so saving the text
    never moves a sentence. One sentence per adjustment goes through
    ``PATCH /{pid}/transcript/{index}`` instead (``api.routers.narration``).

    Takes ``jobs.require_idle`` (409) like every other writer of this record:
    it merges the stored per-sentence adjustments rather than overwriting the
    key blindly, and a transcribe or re-voice job holding the project has its
    own copy of the transcript.

    The answer is the project record plus ``timing_adjustments_dropped``: how
    many sentences lost their adjustment because the saved list no longer
    matches theirs. Usually 0; never silent when it is not.
    """
    require_project(pid, user)
    jobs.require_idle(pid)
    result = store.set_transcript(pid, [seg.model_dump() for seg in body.transcript])
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    updated, dropped = result
    # A saved list whose sentences are not the stored ones (a different count,
    # or different windows) is a different set of sentences, so any adjustments
    # on the old ones are gone. Said out loud, in the answer and in the log.
    audit(PROJECT_TRANSCRIPT_EDIT, user=user, entity="project", entity_id=pid,
          detail=f"{len(body.transcript)} segments"
                 + (f", {dropped} timing adjustment{'' if dropped == 1 else 's'} dropped" if dropped else ""))
    return {**updated, "timing_adjustments_dropped": dropped}


@router.post("/{pid}/generate")
def generate(pid: str, body: GenerateRequest, user: dict = Depends(current_user)):
    """Render a deck (or PDF) project into a narrated MP4.

    Returns a job id to poll at /api/jobs/{id}; when the job is done the video
    is downloadable at /api/projects/{pid}/video and its sidecar files
    (subtitles, extra formats) at /api/projects/{pid}/outputs/{kind}. With
    ``preview_seconds`` > 0 only the first seconds render, to a separate file
    served as the ``preview`` kind; the full video is left as it was.
    """
    record = require_project(pid, user)
    if record.get("kind") not in ("deck", "pdf"):
        raise HTTPException(status_code=400, detail="Only deck and PDF projects can generate a video.")

    preset = get_preset(body.preset)
    provider, voice_id = _narration(body.provider, body.voice_id)
    # The studio defaults are read now, like the narration, so the job is
    # pinned to them whatever an admin changes while it queues.
    render = studio_settings.resolve_render_options(body.model_dump())
    whisper_model = studio_settings.resolve_whisper_model(None) if body.subtitles == "whisper" else ""
    preview = body.preview_seconds > 0

    def work(progress):
        # Imported lazily so the media engine and its heavy deps load only when a
        # generation actually runs, keeping the web process light on startup.
        from services import file_item as file_item_module
        from services import processing
        from utils.helpers import get_output_filename

        output_dir = store.PROJECTS_DIR / pid
        source_path = output_dir / record["source_filename"]

        fi = file_item_module.FileItem(source_path, projects_base=output_dir)
        if record.get("kind") == "pdf":
            fi.load_pdf()
        else:
            fi.load()

        processor = processing.VideoProcessor(
            voice_id=voice_id,
            provider=provider,
            resolution=tuple(preset["resolution"]),
            speed=body.speed,
            video_bitrate=preset["video_bitrate"],
            # The rest of the preset's encode (Q1); a preview keeps the
            # profile and the audio bitrate and swaps the x264 preset for
            # ``ultrafast`` inside the processor.
            x264_preset=preset["x264_preset"],
            h264_profile=preset["profile"],
            audio_bitrate=preset["audio_bitrate"],
            intro_text=body.intro_text,
            intro_subtitle=body.intro_subtitle,
            intro_duration=body.intro_duration,
            outro_text=body.outro_text,
            outro_duration=body.outro_duration,
            subtitles=body.subtitles,
            whisper_model=whisper_model,
            export_webm=body.export_webm,
            export_gif=body.export_gif,
            export_audio_only=body.export_audio_only,
            **render,
        )
        rendered = processor.process_files(
            [fi], output_dir=output_dir, progress=progress, preview_seconds=body.preview_seconds,
        )
        # Where this run's slide images came from (PowerPoint or the title-only
        # Pillow fallback; a PDF's pages are its own renders), for the slide
        # editor - recorded even when the encode failed, the images are there.
        backend = "pdf" if record.get("kind") == "pdf" else getattr(processor, "images_backend", None)
        if backend:
            slides.record_images_source(pid, backend)
        if not rendered:
            raise RuntimeError("The video could not be rendered; the server log has the reason.")

        video_path = get_output_filename(source_path, output_dir)
        # Saved onto the record as it is NOW, not the copy captured at request
        # time. (This used to say a preview and a full render of the same
        # project can run at once; they cannot - ``jobs.start`` refuses a
        # second job while one holds the project. The re-read still matters:
        # ``record_images_source`` above has already written the record during
        # this very job, and a write that passed ``require_idle`` an instant
        # before the job was registered can land on it too. The captured copy
        # would revert either.)
        current = store.get_project(pid) or record
        outputs = dict(current.get("outputs") or {})
        # Lets the UI cache-bust the players: a re-render keeps the file names.
        current["rendered_at"] = datetime.now(timezone.utc).isoformat()
        if preview:
            # A preview stands beside the full render; neither replaces the other.
            outputs["preview"] = video_path.with_stem(video_path.stem + "_preview").name
            current["outputs"] = outputs
            store.save_project(current)
            return {"preview": outputs["preview"]}

        # A full render replaces every sidecar with what it produced (an
        # earlier preview file is still there and stays listed, and so does
        # the Q&A document the AI assistant wrote - it is not a render output).
        current["output_video"] = video_path.name
        kept = {kind: outputs[kind] for kind in ("preview", "qa_doc") if kind in outputs}
        current["outputs"] = {**kept, **processor.outputs}
        store.save_project(current)
        return {"video": video_path.name, "outputs": current["outputs"]}

    # One job per project: a render over a running AI job would save its own
    # stale copy of the notes over everything the AI loop wrote (409 meanwhile).
    job_id = jobs.start("generate", work, project_id=pid, user_id=user["id"])
    audit(PROJECT_GENERATE, user=user, entity="project", entity_id=pid,
          detail=f"job {job_id}, {'preview' if preview else body.preset}, {provider}/{voice_id}")
    return {"job_id": job_id}


@router.post("/{pid}/revoice")
def revoice_video(pid: str, body: RevoiceRequest, user: dict = Depends(current_user)):
    """Re-voice a transcribed video in a new voice (optionally translated).

    Keeps the original frames and swaps the narration track. Returns a job id to
    poll at /api/jobs/{id}; when the job is done the MP4 is downloadable at
    /api/projects/{pid}/revoiced-video.
    """
    record = require_project(pid, user)
    if record.get("kind") != "video":
        raise HTTPException(status_code=400, detail="Only video projects can be re-voiced.")
    if not record.get("transcript"):
        raise HTTPException(status_code=400, detail="Transcribe the video first.")
    # Every sentence muted means no audio at all, and the engine answers that
    # with a bare "Re-voice failed" from inside the job (``_revoice_video``
    # returns False on empty chunks). Refused here with a reason instead,
    # exactly as an empty transcript is.
    if not narration.count_spoken(record.get("transcript")):
        raise HTTPException(
            status_code=400,
            detail="Every sentence is muted, so there would be no narration. Unmute at least one.",
        )

    provider, voice_id = _narration(body.provider, body.voice_id)
    job_id = jobs.start(
        "revoice",
        lambda progress: revoice.revoice_project(
            pid, voice_id, body.speed, body.language, progress, provider=provider,
        ),
        project_id=pid, user_id=user["id"],
    )
    audit(PROJECT_REVOICE, user=user, entity="project", entity_id=pid,
          detail=f"job {job_id}, {provider}/{voice_id}" + (f", {body.language}" if body.language else ""))
    return {"job_id": job_id}


@router.get("/{pid}/video")
def get_video(pid: str, user: dict = Depends(current_user)):
    """Stream the generated MP4 for a project, or 404 if none has been made."""
    record = require_project(pid, user)

    missing = "No generated video for this project."
    filename = record.get("output_video")
    if not filename:
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(str(_project_file(pid, filename, missing)), media_type="video/mp4")


@router.get("/{pid}/outputs/{kind}")
def get_output(pid: str, kind: str, user: dict = Depends(current_user)):
    """Serve one sidecar file of the last generate job: ``srt``, ``vtt``,
    ``webm``, ``gif`` or ``mp3`` as a download, ``preview`` (the short render)
    inline. 404 when the project has none of that kind."""
    record = require_project(pid, user)

    if kind not in OUTPUT_KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown output kind '{kind}'.")
    missing = f"No {kind} output for this project."
    filename = (record.get("outputs") or {}).get(kind)
    if not filename:
        raise HTTPException(status_code=404, detail=missing)

    path = _project_file(pid, filename, missing)
    media_type, disposition = OUTPUT_KINDS[kind]
    return FileResponse(str(path), media_type=media_type, filename=path.name, content_disposition_type=disposition)


# A re-voice rewrites <stem>_revoiced.mp4 and its narration .mp3 IN PLACE, so
# these two URLs name a file whose bytes change under them. Served without a
# Cache-Control header a browser is free to apply HEURISTIC freshness (RFC 9111
# 4.2.2: it may guess a lifetime from Last-Modified) and so answers the next
# request from its own copy without ever asking us - which is how a finished
# re-voice came back as a black frame at 0:00 and a download of bytes that are
# no longer a video. ``no-cache`` does not mean "do not store": it means
# revalidate every time, so the ETag still earns a cheap 304 when the file
# really has not moved. The page cache-busts these URLs as well; this is the
# half that also holds for anything else that fetches them.
MUTABLE_MEDIA_HEADERS = {"Cache-Control": "no-cache"}


@router.get("/{pid}/revoiced-video")
def get_revoiced_video(pid: str, user: dict = Depends(current_user)):
    """Stream the re-voiced MP4 for a project, or 404 if none has been made."""
    record = require_project(pid, user)

    missing = "No re-voiced video for this project."
    filename = record.get("revoiced_video")
    if not filename:
        raise HTTPException(status_code=404, detail=missing)
    return FileResponse(str(_project_file(pid, filename, missing)), media_type="video/mp4",
                        headers=MUTABLE_MEDIA_HEADERS)


# The picture and each voice as its own file. An editor wants them apart: all
# three start at the same zero, so they line up when dropped onto a timeline in
# Camtasia or anything else, which is how you correct by hand what an automatic
# fit gets wrong. Two of the three already existed on disk and were simply never
# served - the imported video, and the audio extracted from it at transcription
# time.
TRACK_KINDS = {
    # kind: (media type, the record field naming the file, what to say when absent)
    # A picture's media type is taken from its own extension: .mov, .mkv, .avi,
    # .webm and .m4v are all importable, and calling them video/mp4 would be a lie.
    "picture": (None, "source_filename", "This project has no source video."),
    # The same words the waveform and the edit use for the same missing file.
    "original-audio": ("audio/wav", None, waveform.NO_AUDIO_MESSAGE),
    "narration": ("audio/mpeg", "narration_audio", "Re-voice the video first."),
}

# Written by the transcription step beside the source video.
ORIGINAL_AUDIO_FILENAME = "audio.wav"


@router.get("/{pid}/tracks/{kind}")
def get_track(pid: str, kind: str, user: dict = Depends(current_user)):
    """Download one track of a video project: ``picture``, ``original-audio``
    or ``narration``. 404 when that track does not exist yet, with a message
    saying which step produces it."""
    record = require_project(pid, user)
    # Only a video project has tracks. Without this the route would happily
    # hand back a deck's .pptx as the "picture", because every kind of project
    # has a source file and only the track name was being checked.
    if record.get("kind") != "video":
        raise HTTPException(status_code=400, detail="Only video projects have separate tracks.")
    if kind not in TRACK_KINDS:
        known = ", ".join(sorted(TRACK_KINDS))
        raise HTTPException(status_code=404, detail=f"Unknown track '{kind}'. Known tracks: {known}.")

    media_type, field, missing = TRACK_KINDS[kind]
    filename = ORIGINAL_AUDIO_FILENAME if field is None else record.get(field)
    if not filename:
        raise HTTPException(status_code=404, detail=missing)

    path = _project_file(pid, filename, missing)
    # The narration is the one track a re-voice rewrites in place; the picture
    # and the extracted original audio are written once and never again. Giving
    # all three the same header keeps the rule in one place rather than making
    # the next reader work out which of them is mutable.
    return FileResponse(str(path), media_type=media_type or _media_type_of(path),
                        filename=path.name, content_disposition_type="attachment",
                        headers=MUTABLE_MEDIA_HEADERS)
