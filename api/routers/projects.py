"""Projects: import decks / PDFs / videos, list them, delete them."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from api.deps import current_user
from services import projects as store

router = APIRouter(prefix="/projects", tags=["projects"])

# Guard against unbounded in-memory reads (the upload is read fully before save).
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


@router.get("")
def list_projects(user: dict = Depends(current_user)):
    return {"projects": store.list_projects()}


@router.post("/import")
async def import_project(file: UploadFile = File(...), user: dict = Depends(current_user)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
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
