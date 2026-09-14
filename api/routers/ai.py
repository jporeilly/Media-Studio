"""The AI assistant on top of the slide editor (porting vertical 3b): Ollama
status, the whole-deck jobs (notes, enhance, QA review, tone, translate,
pacing, Q&A document), the per-slide sync calls (a rewrite proposal, a QA
fix), the analysis, and the Q&A document download.

Thin over ``services.ai_slides``. The routes reuse the slide editor's guards
(``api.routers.slides``): deck/pdf only, 404 for a missing project, and a
409 while a job is attached to the project for anything that writes - which
the AI jobs themselves are (``services.jobs.start``: one job per project,
check-and-submit under one lock, shared with generate, re-voice and render),
so the editor is read-only meanwhile. A job is created only once its request
has been validated (``plan_*``, run OUTSIDE that lock: it probes Ollama and
reads the deck): a bad value is a 400, Ollama being down a 503, and no job
exists for either. A model call that fails during a sync operation is a 502
(the model's fault, not the request's).
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.deps import current_user
from api.routers.slides import call, guard, writable
from api.schemas import (
    AiEnhanceOneRequest, AiEnhanceRequest, AiNotesRequest, AiPacingRequest, AiQaDocRequest, AiQaFixRequest,
    AiToneRequest, AiTranslateRequest,
)
from services import ai_slides, jobs

router = APIRouter(prefix="/projects", tags=["ai"])


def _ai(func):
    """A service call with the AI failures mapped: Ollama unreachable 503, a
    failed model call 502; the slide editor's own mappings otherwise."""
    try:
        return call(func)
    except ai_slides.AiUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ai_slides.AiError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


def _start(pid: str, kind: str, plan, user: dict):
    """409 while a job holds the project, validate (``plan`` raises for a bad
    request; it probes Ollama and reads the deck, so it runs outside the
    start lock), then attach the job - ``jobs.start`` checks again under the
    lock, so two requests cannot both start one."""
    writable(pid)
    work = _ai(plan)
    return {"job_id": jobs.start(kind, work, project_id=pid, user_id=user["id"])}


@router.get("/{pid}/ai/status")
def ai_status(pid: str, user: dict = Depends(current_user)):
    """Whether Ollama answers, the configured model, and whether this
    project's slide images may be shown to it (the editor's AI banner)."""
    guard(pid)
    return call(lambda: ai_slides.status(pid))


@router.post("/{pid}/ai/notes")
def ai_notes(pid: str, body: AiNotesRequest, user: dict = Depends(current_user)):
    """Generate speaker notes from each slide's title, text and (optionally)
    image - the slides without notes by default. A job of kind ``ai-notes``."""
    return _start(pid, "ai-notes", lambda: ai_slides.plan_notes(
        pid, mode="notes", scope=body.scope, slide_indexes=body.slide_indexes, use_vision=body.use_vision,
    ), user)


@router.post("/{pid}/ai/enhance")
def ai_enhance(pid: str, body: AiEnhanceRequest, user: dict = Depends(current_user)):
    """Rewrite the notes for natural narration (every slide by default; a
    slide without notes gets them from its content). Kind ``ai-enhance``."""
    return _start(pid, "ai-enhance", lambda: ai_slides.plan_notes(
        pid, mode="enhance", scope=body.scope, slide_indexes=body.slide_indexes, use_vision=body.use_vision,
    ), user)


@router.post("/{pid}/ai/qa")
def ai_qa(pid: str, user: dict = Depends(current_user)):
    """Review every slide's notes for grammar, tone, flow and transitions;
    the result lands on the project as ``qa_review``. Kind ``ai-qa``."""
    return _start(pid, "ai-qa", lambda: ai_slides.plan_qa(pid), user)


@router.post("/{pid}/ai/tone")
def ai_tone(pid: str, body: AiToneRequest, user: dict = Depends(current_user)):
    """Rewrite every note for an audience (a preset, or a custom instruction). Kind ``ai-tone``."""
    return _start(pid, "ai-tone", lambda: ai_slides.plan_tone(pid, body.tone, body.custom_prompt), user)


@router.post("/{pid}/ai/translate")
def ai_translate(pid: str, body: AiTranslateRequest, user: dict = Depends(current_user)):
    """Translate every note in place; the result may name a matching voice. Kind ``ai-translate``."""
    return _start(pid, "ai-translate", lambda: ai_slides.plan_translate(
        pid, body.language, match_voice=body.match_voice, provider=body.provider,
    ), user)


@router.post("/{pid}/ai/pacing")
def ai_pacing(pid: str, body: AiPacingRequest | None = None, user: dict = Depends(current_user)):
    """Insert narration pauses: the rules run now and answer with the tally;
    ``use_ai`` places them with the model per slide as a job (``ai-pacing``)."""
    if body and body.use_ai:
        return _start(pid, "ai-pacing", lambda: ai_slides.plan_pacing_ai(pid), user)
    writable(pid)
    return call(lambda: ai_slides.pacing_rules(pid))


@router.post("/{pid}/ai/qa-doc")
def ai_qa_doc(pid: str, body: AiQaDocRequest | None = None, user: dict = Depends(current_user)):
    """Write a Q&A document (anticipated questions with answers) from the notes. Kind ``ai-qa-doc``."""
    count = body.num_questions if body else 10
    return _start(pid, "ai-qa-doc", lambda: ai_slides.plan_qa_doc(pid, count), user)


@router.post("/{pid}/ai/analyze")
def ai_analyze(pid: str, user: dict = Depends(current_user)):
    """Score the deck for video (text density, notes coverage, visuals): the
    rules always, the model's suggestions when Ollama answers. Synchronous."""
    guard(pid)
    return _ai(lambda: ai_slides.analyze(pid))


@router.post("/{pid}/slides/{index}/ai/enhance")
def ai_enhance_one(pid: str, index: int, body: AiEnhanceOneRequest | None = None, user: dict = Depends(current_user)):
    """A rewrite of one slide's notes as a proposal - nothing is saved; the
    editor shows it as a draft with Revert. Synchronous (one model call)."""
    guard(pid)
    body = body or AiEnhanceOneRequest()
    return _ai(lambda: ai_slides.enhance_one(pid, index, use_vision=body.use_vision, notes=body.notes))


@router.post("/{pid}/slides/{index}/ai/qa-fix")
def ai_qa_fix(pid: str, index: int, body: AiQaFixRequest, user: dict = Depends(current_user)):
    """Fix one QA criterion on one slide (saved; the review marks it fixed). Returns the slide."""
    writable(pid)
    return _ai(lambda: ai_slides.qa_fix(pid, index, body.criterion, body.issue))


@router.get("/{pid}/export/qa")
def export_qa_doc(pid: str, user: dict = Depends(current_user)):
    """Download the Q&A document the ``ai-qa-doc`` job wrote; 404 until one has."""
    guard(pid)
    path = call(lambda: ai_slides.qa_doc_path(pid))
    if path is None:
        raise HTTPException(status_code=404, detail="No Q&A document for this project yet.")
    return FileResponse(str(path), media_type="text/plain", filename=path.name, content_disposition_type="attachment")
