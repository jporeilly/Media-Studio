"""Projects: import decks / PDFs / videos, list them, delete them."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from api.deps import current_user
from api.schemas import GenerateRequest, RevoiceRequest, TranscribeRequest, TranscriptUpdate
from services import jobs, projects as store, revoice, studio_settings, transcription
from services.output_presets import get_preset

router = APIRouter(prefix="/projects", tags=["projects"])

# Guard against unbounded in-memory reads (the upload is read fully before save).
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


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
    job_id = jobs.submit(
        "transcribe",
        lambda progress: transcription.transcribe_project(pid, model or None, progress),
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
    is downloadable at /api/projects/{pid}/video.
    """
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")
    if record.get("kind") not in ("deck", "pdf"):
        raise HTTPException(status_code=400, detail="Only deck and PDF projects can generate a video.")

    preset = get_preset(body.preset)
    provider, voice_id = _narration(body.provider, body.voice_id)

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
        )
        processor.process_files([fi], output_dir=output_dir, progress=progress)

        video_path = get_output_filename(source_path, output_dir)
        record["output_video"] = video_path.name
        store.save_project(record)
        return {"video": video_path.name}

    job_id = jobs.submit("generate", work)
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
    job_id = jobs.submit(
        "revoice",
        lambda progress: revoice.revoice_project(
            pid, voice_id, body.speed, body.language, progress, provider=provider,
        ),
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
