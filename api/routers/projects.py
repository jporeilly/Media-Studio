"""Projects: import decks / PDFs / videos, list them, delete them."""

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from api.deps import current_user
from api.schemas import GenerateRequest, RevoiceRequest, TranscribeRequest, TranscriptUpdate
from services import jobs, projects as store, revoice, slides, studio_settings, transcription
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


@router.get("")
def list_projects(user: dict = Depends(current_user)):
    return {"projects": store.list_projects()}


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
        return store.import_upload(file.filename or "upload", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{pid}")
def get_project(pid: str, user: dict = Depends(current_user)):
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")
    return record


@router.delete("/{pid}", status_code=204)
def delete_project(pid: str, user: dict = Depends(current_user)):
    if not store.delete_project(pid):
        raise HTTPException(status_code=404, detail="Project not found.")


@router.post("/{pid}/transcribe")
def transcribe(pid: str, body: TranscribeRequest | None = None, user: dict = Depends(current_user)):
    """Start transcribing a video project. Returns a job id to poll at /api/jobs/{id}."""
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")
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
    return {"job_id": job_id}


@router.patch("/{pid}/transcript")
def update_transcript(pid: str, body: TranscriptUpdate, user: dict = Depends(current_user)):
    """Replace the edited transcript segments on a project."""
    updated = store.set_transcript(pid, [seg.model_dump() for seg in body.transcript])
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return updated


@router.post("/{pid}/generate")
def generate(pid: str, body: GenerateRequest, user: dict = Depends(current_user)):
    """Render a deck (or PDF) project into a narrated MP4.

    Returns a job id to poll at /api/jobs/{id}; when the job is done the video
    is downloadable at /api/projects/{pid}/video and its sidecar files
    (subtitles, extra formats) at /api/projects/{pid}/outputs/{kind}. With
    ``preview_seconds`` > 0 only the first seconds render, to a separate file
    served as the ``preview`` kind; the full video is left as it was.
    """
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")
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
        # time: a preview and a full render of the same project can run at
        # once, and the copy would overwrite whatever the other job saved.
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
    return {"job_id": job_id}


@router.post("/{pid}/revoice")
def revoice_video(pid: str, body: RevoiceRequest, user: dict = Depends(current_user)):
    """Re-voice a transcribed video in a new voice (optionally translated).

    Keeps the original frames and swaps the narration track. Returns a job id to
    poll at /api/jobs/{id}; when the job is done the MP4 is downloadable at
    /api/projects/{pid}/revoiced-video.
    """
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")
    if record.get("kind") != "video":
        raise HTTPException(status_code=400, detail="Only video projects can be re-voiced.")
    if not record.get("transcript"):
        raise HTTPException(status_code=400, detail="Transcribe the video first.")

    provider, voice_id = _narration(body.provider, body.voice_id)
    job_id = jobs.start(
        "revoice",
        lambda progress: revoice.revoice_project(
            pid, voice_id, body.speed, body.language, progress, provider=provider,
        ),
        project_id=pid, user_id=user["id"],
    )
    return {"job_id": job_id}


@router.get("/{pid}/video")
def get_video(pid: str, user: dict = Depends(current_user)):
    """Stream the generated MP4 for a project, or 404 if none has been made."""
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")

    filename = record.get("output_video")
    if not filename:
        raise HTTPException(status_code=404, detail="No generated video for this project.")

    # Resolve the path and confirm it stays under the project directory, so a
    # tampered ``output_video`` can never read a file outside the store.
    base = (store.PROJECTS_DIR / pid).resolve()
    video_path = (base / filename).resolve()
    if base not in video_path.parents or not video_path.is_file():
        raise HTTPException(status_code=404, detail="No generated video for this project.")

    return FileResponse(str(video_path), media_type="video/mp4")


@router.get("/{pid}/outputs/{kind}")
def get_output(pid: str, kind: str, user: dict = Depends(current_user)):
    """Serve one sidecar file of the last generate job: ``srt``, ``vtt``,
    ``webm``, ``gif`` or ``mp3`` as a download, ``preview`` (the short render)
    inline. 404 when the project has none of that kind."""
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")

    if kind not in OUTPUT_KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown output kind '{kind}'.")
    filename = (record.get("outputs") or {}).get(kind)
    if not filename:
        raise HTTPException(status_code=404, detail=f"No {kind} output for this project.")

    # Same guard as get_video: the resolved path must stay under the project dir.
    base = (store.PROJECTS_DIR / pid).resolve()
    path = (base / filename).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail=f"No {kind} output for this project.")

    media_type, disposition = OUTPUT_KINDS[kind]
    return FileResponse(str(path), media_type=media_type, filename=path.name, content_disposition_type=disposition)


@router.get("/{pid}/revoiced-video")
def get_revoiced_video(pid: str, user: dict = Depends(current_user)):
    """Stream the re-voiced MP4 for a project, or 404 if none has been made."""
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")

    filename = record.get("revoiced_video")
    if not filename:
        raise HTTPException(status_code=404, detail="No re-voiced video for this project.")

    # Same guard as get_video: the resolved path must stay under the project dir.
    base = (store.PROJECTS_DIR / pid).resolve()
    video_path = (base / filename).resolve()
    if base not in video_path.parents or not video_path.is_file():
        raise HTTPException(status_code=404, detail="No re-voiced video for this project.")

    return FileResponse(str(video_path), media_type="video/mp4")
