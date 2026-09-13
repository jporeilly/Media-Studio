"""Projects: import decks / PDFs / videos, list them, delete them."""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from api.deps import current_user
from api.schemas import TranscribeRequest, TranscriptUpdate
from services import jobs, projects as store, transcription

router = APIRouter(prefix="/projects", tags=["projects"])

# Guard against unbounded in-memory reads (the upload is read fully before save).
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


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
    model = body.model if body else None
    job_id = jobs.submit(
        "transcribe",
        lambda progress: transcription.transcribe_project(pid, model, progress),
    )
    return {"job_id": job_id}


@router.patch("/{pid}/transcript")
def update_transcript(pid: str, body: TranscriptUpdate, user: dict = Depends(current_user)):
    """Replace the edited transcript segments on a project."""
    updated = store.set_transcript(pid, [seg.model_dump() for seg in body.transcript])
    if updated is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return updated
