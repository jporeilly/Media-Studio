"""The AI assistant over the slide editor: Ollama-powered speaker-note work on
a deck or PDF project, ported from SlideStudio's GUI actions
(``gui/components/generation_ai_actions.py`` and ``preview_panel.py``) into a
service the API runs as jobs and sync calls.

Everything reads and writes through ``services.slides`` (its per-project lock,
its validation, the undo history) - nothing here opens project.json itself.
The engine pieces are the byte-identical ports in ``core``: the per-slide
vision prompts go through ``ollama_client.generate``, as do the tone,
translation and AI-pacing prompts (rebuilt from ``tone_adapter``,
``translator`` and ``auto_pacing`` - their own ``requests`` loops hand the
ORIGINAL text back on any failure, which would report a dead server as "40
unchanged"); the Q&A document and the analysis go through ``qa_generator``
and ``slide_analyzer``.

Whole-deck work runs as jobs. ``plan_*`` validates the request now (a
``ValueError`` is the route's 400, ``AiUnavailable`` its 503 - no job is
created for a request that cannot run) and returns the work callable the route
hands to ``services.jobs.start`` with the project attached, so the editor is
read-only and no other job can start meanwhile. Each job warms the model
with one short call first (a cold load takes longer than a per-slide call
should), then makes one model call per slide, reports ``i/total`` progress,
counts per-slide failures rather than stopping, saves each result as it comes
so a cancelled or failed run keeps what it had written, and checks the job's
cancel flag between slides. Only Ollama going away stops a loop early
(``AiUnavailable``: the server no longer answers a probe): every remaining
slide would otherwise wait out its own timeout.

Vision: a slide's image goes into the prompt only when the configured model is
in the vision list (the engine catalogue's Vision entries plus the usual
Ollama vision families) AND the project's previews are real renders
(``images_source`` powerpoint or pdf). The title-only Pillow fallback is never
shown to a model - a white card with a title reads as an empty slide - and
previews rendered before the source was recorded are asked to be rendered
again (porting spec, trap 2).

The QA review persists on the outer record as ``qa_review`` (read back through
``services.slides.slides_payload``), written only when a run succeeds so a
failed pass keeps the previous verdict; the Q&A document as ``outputs.qa_doc``
(served by ``GET /api/projects/{pid}/export/qa``).
"""

import json
import re
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Callable

from core import auto_pacing, ollama_client, qa_generator, slide_analyzer, tone_adapter, translator
from core.tts_provider import Voice, suggest_voice_for_language
from services import jobs, projects as store, slides, studio_settings
from services import voices as voice_lists
from utils.config import config
from utils.logger import get_logger

logger = get_logger("AI_SLIDES")

CONNECT_TIMEOUT = 3.0  # the reachability probe (status, before a job is created, after a dropped call)
SLIDE_TIMEOUT = 120.0  # one per-slide generation, as SlideStudio ran it
QA_TIMEOUT = 180.0  # one QA pass over up to QA_CHUNK_SIZE slides
QA_CHUNK_SIZE = 20  # a review sends every note in one prompt; a deck is split into passes this size...
QA_CHUNK_CHARS = 12_000  # ...and no pass carries more note text than this (Ollama's num_ctx would truncate it)
MAX_ISSUE_CHARS = 2000

QA_CRITERIA = ("grammar", "tone", "flow", "transitions")
QA_LABELS = {"grammar": "Grammar", "tone": "Tone", "flow": "Flow", "transitions": "Transitions"}
# What the model writes for a criterion with nothing to report (SlideStudio's set).
NO_ISSUE = frozenset({"none", "no issues", "n/a", "-", "ok", "good", "no issue", ""})
NO_ISSUE_TEXT = "None"

# The model families that see images: the engine catalogue's Vision entries
# (gemma3, llava, moondream) and the other vision models Ollama ships. A
# configured "gemma3:12b-it-qat" counts by its family.
VISION_FAMILIES = frozenset(
    entry["name"].split(":", 1)[0] for entry in ollama_client.MODEL_CATALOG if entry["category"] == "Vision"
) | {"llama3.2-vision", "qwen2.5vl", "qwen2-vl", "minicpm-v", "llava-llama3", "bakllava"}
REAL_RENDERS = ("powerpoint", "pdf")

JOB_KINDS = ("ai-notes", "ai-enhance", "ai-qa", "ai-tone", "ai-translate", "ai-pacing", "ai-qa-doc")
CUSTOM_TONE = "Custom"
NO_SYSTEM = ""  # a prompt sent without a system prompt (the engine's own loops send none)
WARM_UP_PROMPT = "Reply with the single word OK."

# -- the prompts, as SlideStudio built them ---------------------------------

IMAGE_PREAMBLE = {
    "notes": "I'm showing you the slide image. Describe any visual elements and create speaker notes that reference them.",
    "enhance": (
        "I'm showing you the slide image. Describe any visual elements (charts, diagrams, images, layouts) "
        "and incorporate them into the speaker notes."
    ),
}
NOTES_INSTRUCTION = "Generate clear, engaging speaker notes for this slide. Write as if speaking to an audience."
ENHANCE_INSTRUCTION = "Improve and enhance these speaker notes based on the slide content above."
GENERATE_INSTRUCTION = "Generate clear, engaging speaker notes for this slide."

QA_SYSTEM = (
    "You are a meticulous proofreader and professional presentation coach. Check every word for spelling, "
    "every sentence for grammar, every slide for tone and flow. Be strict — flag real issues with specific "
    "quotes. Respond with ONLY valid JSON. No markdown, no explanation."
)
QA_FIX_SYSTEM = "You are a precise text editor. Make only the specific change requested. Preserve everything else exactly as-is."
QA_CONSTRAINTS = {
    "grammar": "Fix ONLY grammar and spelling errors. Do NOT change the content, length, tone, or structure.",
    "tone": "Adjust ONLY the tone and terminology for consistency. Do NOT change the content length or fix grammar.",
    "flow": "Improve ONLY the flow and logical progression. Do NOT change the length, tone, or fix grammar.",
    "transitions": (
        "Improve ONLY the topic connections and narrative linking between slides (e.g., add connecting phrases, "
        "signposting). Do NOT change tone, or fix grammar."
    ),
}

# core/auto_pacing.ai_pacing's prompt and its acceptance rule (a shorter answer
# lost words: the rules place the pauses instead).
PACING_INSTRUCTION = (
    "Add natural pauses (represented by '...') to this narration text. "
    "Place pauses: after introductory phrases, before key points, "
    "between topic transitions, and around questions. "
    "Don't change any words, only add '...' where pauses should go. "
    "Return only the text with pauses added."
)
PACING_MIN_RATIO = 0.8


class AiError(RuntimeError):
    """One model call failed; the message is meant for the user."""


class AiUnavailable(AiError):
    """Ollama could not be reached: nothing more will succeed, so a job stops."""


# -- configuration ---------------------------------------------------------

def base_url() -> str:
    return (config.ollama_url or ollama_client.DEFAULT_URL).rstrip("/")


def model() -> str:
    """The configured model (Settings › Studio), else the studio's fallback."""
    return studio_settings.ollama_model()


def system_prompt() -> str:
    """The studio's own system prompt when one is set, else the engine's."""
    return config.ollama_system_prompt or ollama_client.DEFAULT_SYSTEM_PROMPT


def vision_capable(name: str) -> bool:
    return (name or "").split(":", 1)[0].strip().lower() in VISION_FAMILIES


def reachable() -> bool:
    return bool(ollama_client.check_connection(base_url(), timeout=CONNECT_TIMEOUT))


def _require_reachable() -> None:
    if not reachable():
        raise AiUnavailable(
            f"Ollama is not reachable at {base_url()}. Start it (or fix ollama_url in data/config.json) and try again."
        )


def _installed(name: str, models: list[str]) -> bool:
    names = set(models)
    return name in names or f"{name}:latest" in names or (name.endswith(":latest") and name[:-7] in names)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- status and the vision gate --------------------------------------------

def vision_for_project(pid: str) -> tuple[bool, str | None]:
    """Whether a slide image may go into a prompt for ``pid``, with the reason
    when not: a model outside the vision list, the title-only Pillow
    fallback, previews rendered before their source was recorded (render
    them again), or previews not rendered yet."""
    name = model()
    if not vision_capable(name):
        return False, (
            f"{name} is not a vision model (vision models: gemma3, llava, moondream, llama3.2-vision, "
            "qwen2.5vl, minicpm-v)"
        )
    record = slides.project_record(pid)
    source = record.get("images_source")
    if source == "pillow":
        return False, (
            "the slide previews are the title-only fallback (PowerPoint was not available when they were "
            "rendered), so the model would see blank slides"
        )
    if not slides.images_ready(pid):
        return False, "the slide previews have not been rendered yet — render them first"
    if source not in REAL_RENDERS:
        return False, "the slide previews were rendered before their source was recorded — render them again to enable vision"
    return True, None


def tones() -> list[dict]:
    """The tone presets the Tone action offers, plus Custom."""
    presets = [{"name": name, "description": text} for name, text in tone_adapter.get_available_tones().items()]
    return presets + [{"name": CUSTOM_TONE, "description": "Your own instruction"}]


def status(pid: str | None = None) -> dict:
    """What the editor's AI banner shows: whether Ollama answers (3 s), the
    models it holds, the configured model and whether it is pulled, whether
    it can see images, and - for ``pid`` - whether this project's previews may
    be shown to it (``vision_usable`` / ``vision_reason``)."""
    url, name = base_url(), model()
    available = reachable()
    models = [m.name for m in ollama_client.list_models(url)] if available else []
    if pid is None:
        capable = vision_capable(name)
        usable, reason = capable, (None if capable else f"{name} is not a vision model")
    else:
        usable, reason = vision_for_project(pid)
    return {
        "available": available,
        "base_url": url,
        "configured_model": name,
        "model_installed": _installed(name, models) if available else None,
        "models": models,
        "vision_capable": vision_capable(name),
        "vision_usable": usable,
        "vision_reason": reason,
        "tones": tones(),
    }


# -- one model call ----------------------------------------------------------

def build_prompt(title: str | None, body: str | None, notes: str, *, image: bool, mode: str) -> str:
    """SlideStudio's one prompt shape: the image preamble (only when an image
    goes with it), ``Slide title:``, ``Slide content:``, then for ``enhance``
    the existing notes and the improve instruction (or the generate one when
    there are none), for ``notes`` the generate-from-scratch instruction."""
    if mode not in IMAGE_PREAMBLE:
        raise ValueError(f"Unknown prompt mode '{mode}'.")
    parts = []
    if image:
        parts.append(IMAGE_PREAMBLE[mode])
    if title:
        parts.append(f"Slide title: {title}")
    if body:
        parts.append(f"Slide content:\n{body}")
    if mode == "notes":
        parts.append(NOTES_INSTRUCTION)
    elif notes:
        parts.append(f"Existing speaker notes:\n{notes}")
        parts.append(ENHANCE_INSTRUCTION)
    else:
        parts.append(GENERATE_INSTRUCTION)
    return "\n\n".join(parts)


def tone_prompt(note: str, tone: str, custom_prompt: str = "") -> str:
    """``core.tone_adapter.adapt_notes``'s prompt for one note."""
    base = custom_prompt if tone == CUSTOM_TONE else tone_adapter.TONE_PRESETS[tone]["prompt"]
    return f"{base}\n\nOriginal text:\n{note}\n\nRewritten text:"


def translate_prompt(note: str, language: str) -> str:
    """``core.translator.translate_text``'s prompt for one note (bracketed
    markup such as [pause:1s] is asked to be kept in place)."""
    guard = (
        " Keep any bracketed markup such as [pause:1s], [break] or [emphasis] "
        "exactly as written and in the same position."
        if translator._has_markup(note)
        else ""
    )
    return (
        f"Translate the text below into {language}. "
        f"Output only the translation — no preamble, quotes, or explanation."
        f"{guard}\n\nText:\n{note}\n\nTranslation:"
    )


def pacing_prompt(note: str) -> str:
    """``core.auto_pacing.ai_pacing``'s prompt for one note."""
    return f"{PACING_INSTRUCTION}\n\n{note}"


def _http_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
        detail = json.loads(body).get("error") or body
    except Exception:  # the body is whatever the server sent; the status line is enough
        detail = exc.reason
    return str(detail).strip()[:200]


def _dropped(exc: Exception, url: str) -> AiError:
    """A dropped connection: ``AiUnavailable`` only when the server no longer
    answers a probe; a per-slide failure while it still does."""
    if reachable():
        return AiError(f"The connection to Ollama failed during the call ({exc}); the server still answers.")
    return AiUnavailable(f"Ollama is not reachable at {url} ({exc}).")


def _call(prompt: str, *, images: list[str] | None = None, timeout: float = SLIDE_TIMEOUT, system: str | None = None) -> str:
    """``ollama_client.generate`` with its failures turned into messages: an
    HTTP error names the model (a model not pulled is a 404), a timeout says
    how long it waited, a dropped connection is ``AiUnavailable`` only when a
    probe finds the server gone. ``system`` None is the studio's / engine's
    system prompt; ``NO_SYSTEM`` sends none (the engine's tone, translation
    and pacing prompts carry their whole instruction themselves)."""
    url, name = base_url(), model()
    try:
        return ollama_client.generate(
            prompt=prompt, model=name, system=system_prompt() if system is None else system,
            base_url=url, timeout=timeout, images=images,
        )
    except urllib.error.HTTPError as exc:
        raise AiError(f"Ollama answered {exc.code} for model '{name}': {_http_detail(exc)}") from exc
    except TimeoutError as exc:
        raise AiError(f"The model did not answer within {timeout:.0f} s.") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError) or "timed out" in str(exc.reason).lower():
            raise AiError(f"The model did not answer within {timeout:.0f} s.") from exc
        raise _dropped(exc.reason, url) from exc
    except OSError as exc:  # a dropped connection (ConnectionError family) or a socket error
        raise _dropped(exc, url) from exc
    except ValueError as exc:  # json.JSONDecodeError: not an Ollama server behind that URL
        raise AiError(f"Ollama answered with something that is not JSON: {exc}") from exc


def _warm_up(progress) -> None:
    """One short call before a loop, so the model's cold load (a minute for a
    12B model) is paid once, up front, and not by the first slide's timeout."""
    progress(0.0, f"Loading {model()}…")
    try:
        _call(WARM_UP_PROMPT, system=NO_SYSTEM)
    except AiUnavailable:
        raise
    except AiError as exc:
        raise AiError(f"The model could not be loaded: {exc}") from exc


# -- what a prompt is built from --------------------------------------------

@dataclass
class SlideSource:
    """One slide as the prompts see it. ``image`` is a server-side path (never
    serialised): the file goes into the request body as base64."""
    index: int
    notes: str
    title: str | None
    body: str | None
    image: Path | None


def _pdf_texts(source: Path) -> list[tuple[str | None, str | None]]:
    """A PDF's page texts as (title, body) the way ``core.pdf_reader`` splits
    them (the first line, the rest), without rendering the pages again."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(source))
    except Exception as exc:
        logger.warning("PDF text extraction failed for %s: %s", source.name, exc)
        return []
    out = []
    try:
        for page in doc:
            text = page.get_text().strip()
            lines = text.split("\n") if text else []
            title = lines[0] if lines else None
            body = "\n".join(lines[1:]).strip() if len(lines) > 1 else None
            out.append((title, body or None))
    finally:
        doc.close()
    return out


def slide_sources(pid: str) -> list[SlideSource]:
    """Every slide's notes, title, body text and rendered image (when the file
    exists), from one open of the inner project. A deck's title and text come
    from the editor's own listing; a PDF's from its page text (the editor
    lists none for a PDF)."""
    record = slides.project_record(pid)
    listed = slides.slide_context(pid)
    texts = _pdf_texts(slides.source_path(pid)) if record["kind"] == "pdf" else None
    out = []
    for item in listed:
        title, body = item["title"], item["body_text"]
        if texts is not None and item["index"] < len(texts):
            title, body = texts[item["index"]]
        out.append(SlideSource(item["index"], item["speaker_notes"] or "", title, body, item["image"]))
    return out


def _check_index(index, count: int) -> int:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
        raise ValueError(f"No slide with index {index!r}: the project has {count} slides (0 to {count - 1}).")
    return index


# -- the per-slide loops -----------------------------------------------------

def _tally(total: int, **extra) -> dict:
    return {"total": total, "done": 0, "failed": 0, "skipped": 0, "unchanged": 0, "errors": [], "cancelled": False, **extra}


def _fail(result: dict, index: int, why: str) -> None:
    result["failed"] += 1
    result["errors"].append(f"Slide {index + 1}: {why}")


def _summary(label: str, result: dict) -> str:
    text = f"{label}: {result['done']} of {result['total']} slides"
    for key in ("failed", "skipped", "unchanged"):
        if result[key]:
            text += f", {result[key]} {key}"
    return text


def _stopped(exc: AiUnavailable, n: int, total: int) -> AiUnavailable:
    return AiUnavailable(f"{exc} Stopped after {n} of {total} slides; the notes written so far are saved.")


def _notes_loop(pid: str, indexes: list[int], *, mode: str, vision: bool, label: str, progress) -> dict:
    """Generate (``notes``) or rewrite (``enhance``) the notes of ``indexes``,
    one model call each, saving every answer as it arrives."""
    sources = {s.index: s for s in slide_sources(pid)}
    total = len(indexes)
    result = _tally(total, vision=vision)
    _warm_up(progress)
    for n, index in enumerate(indexes):
        if jobs.cancel_requested_here():
            result["cancelled"] = True
            progress(n / total, f"{label}: cancelled after {n} of {total} slides")
            return result
        progress(n / total, f"{label}: {n + 1}/{total} slides…")
        src = sources.get(index)
        if src is None:
            result["skipped"] += 1
            continue
        image = src.image if vision and src.image else None
        notes = src.notes.strip() if mode == "enhance" else ""
        if not src.title and not src.body and not image and not notes:
            result["skipped"] += 1  # nothing to work from, as SlideStudio skipped it
            continue
        prompt = build_prompt(src.title, src.body, notes, image=bool(image), mode=mode)
        try:
            text = _call(prompt, images=[str(image)] if image else None).strip()
        except AiUnavailable as exc:
            raise _stopped(exc, n, total) from exc
        except AiError as exc:
            _fail(result, index, str(exc))
            continue
        if not text:
            _fail(result, index, "the model returned nothing")
            continue
        try:
            slides.update_slide(pid, index, speaker_notes=text, ai_enhanced=True)
        except ValueError as exc:
            _fail(result, index, str(exc))
            continue
        result["done"] += 1
    progress(1.0, _summary(label, result))
    return result


def _rewrite_loop(pid: str, indexes: list[int], *, label: str, progress, rewrite: Callable[[str], str]) -> dict:
    """Run a per-note rewrite (tone, translation, pacing: one ``_call`` each)
    over ``indexes``, saving each changed note as it comes. A failed call is
    a counted failure, a dead server stops the loop; only an answer identical
    to the note is ``unchanged``."""
    current = {s["index"]: s["speaker_notes"] or "" for s in slides.list_slides(pid)}
    total = len(indexes)
    result = _tally(total)
    _warm_up(progress)
    for n, index in enumerate(indexes):
        if jobs.cancel_requested_here():
            result["cancelled"] = True
            progress(n / total, f"{label}: cancelled after {n} of {total} slides")
            return result
        progress(n / total, f"{label}: {n + 1}/{total} slides…")
        note = current.get(index, "")
        if not note.strip():
            result["skipped"] += 1
            continue
        try:
            new = (rewrite(note) or "").strip()
        except AiUnavailable as exc:
            raise _stopped(exc, n, total) from exc
        except AiError as exc:
            _fail(result, index, str(exc))
            continue
        if not new:
            _fail(result, index, "the model returned nothing")
            continue
        if new == note.strip():
            result["unchanged"] += 1
            continue
        try:
            slides.update_slide(pid, index, speaker_notes=new, ai_enhanced=True)
        except ValueError as exc:
            _fail(result, index, str(exc))
            continue
        result["done"] += 1
    progress(1.0, _summary(label, result))
    return result


def _targets(sources: list[SlideSource], scope: str, slide_indexes) -> list[int]:
    if scope not in ("empty", "all"):
        raise ValueError("scope must be 'empty' (slides without notes) or 'all'.")
    if slide_indexes:
        count = len(sources)
        picked = [sources[_check_index(i, count)] for i in dict.fromkeys(slide_indexes)]
    else:
        picked = list(sources)
    if scope == "empty":
        picked = [s for s in picked if not s.notes.strip()]
    return [s.index for s in picked]


def _with_notes(pid: str) -> list[int]:
    return [s["index"] for s in slides.list_slides(pid) if (s["speaker_notes"] or "").strip()]


# -- the jobs ------------------------------------------------------------------

def plan_notes(pid: str, *, mode: str, scope: str = "empty", slide_indexes=None, use_vision: bool = False):
    """The ``ai-notes`` (``mode="notes"``: notes from the slide's title, text
    and image) and ``ai-enhance`` (``mode="enhance"``: rewrite for natural
    narration) jobs. ``scope`` picks the slides without notes or all of them;
    ``slide_indexes`` narrows either. Vision is used only when requested AND
    usable for the project (the result says which)."""
    if mode not in IMAGE_PREAMBLE:
        raise ValueError(f"Unknown mode '{mode}'.")
    indexes = _targets(slide_sources(pid), scope, slide_indexes)
    if not indexes:
        raise ValueError("Every slide already has notes." if scope == "empty" else "This project has no slides.")
    vision, reason = vision_for_project(pid) if use_vision else (False, None)
    label = "Generate notes" if mode == "notes" else "Enhance"
    _require_reachable()

    def work(progress):
        result = _notes_loop(pid, indexes, mode=mode, vision=vision, label=label, progress=progress)
        result["vision_reason"] = reason if use_vision and not vision else None
        return result

    return work


def chunked(items: list, size: int = QA_CHUNK_SIZE, max_chars: int | None = None, text=None) -> list[list]:
    """``items`` in as few consecutive chunks of at most ``size`` as possible,
    balanced so no chunk is much shorter than the others (38 → 19 + 19);
    with ``max_chars`` a chunk whose texts (``text(item)``) exceed it is split
    further, so a pass never carries more than the model's context holds."""
    n = len(items)
    if n == 0:
        return []
    count = -(-n // size)
    base, extra = divmod(n, count)
    out, start = [], 0
    for i in range(count):
        length = base + (1 if i < extra else 0)
        out.append(items[start:start + length])
        start += length
    if not max_chars:
        return out
    measure = text or (lambda item: item)
    capped = []
    for chunk in out:
        current, chars = [], 0
        for item in chunk:
            length = len(measure(item))
            if current and chars + length > max_chars:
                capped.append(current)
                current, chars = [], 0
            current.append(item)
            chars += length
        capped.append(current)
    return capped


def qa_prompt(chunk: list[tuple[int, str]]) -> str:
    """SlideStudio's QA prompt for one pass: the strict-JSON array skeleton
    with one entry per slide IN ORDER, the four categories, the rubric."""
    notes_block = "\n\n".join(f"--- Slide {index + 1} ---\n{notes}" for index, notes in chunk)
    prompt = (
        f"Review the following speaker notes across {len(chunk)} slides.\n\n"
        "You MUST respond with ONLY valid JSON (no markdown, no extra text).\n"
        "Use this exact structure — 'slides' is an ARRAY with one entry per slide IN ORDER:\n"
        "{\n"
        '  "score": <overall quality 1-10>,\n'
        '  "slides": [\n'
    )
    for index, _notes in chunk:
        prompt += f'    {{"slide": {index + 1}, "grammar": "...", "tone": "...", "flow": "...", "transitions": "..."}},\n'
    prompt += (
        "  ]\n"
        "}\n\n"
        "For each slide entry, fill in each category with a SHORT, SPECIFIC description of the issue found "
        "(quote the problematic word/phrase if possible), or \"None\" if no issue in that category.\n\n"
        "Categories — check EACH carefully:\n"
        "- grammar: Spelling errors, typos, punctuation mistakes, incorrect verb tense, "
        "subject-verb disagreement, run-on sentences, sentence fragments, "
        "missing articles (a/an/the), wrong word usage (their/there/they're, its/it's), "
        "capitalization errors, double words, missing commas or periods\n"
        "- tone: Inconsistent formality level (mixing casual and formal language), "
        "jargon or acronyms used without explanation, overly technical or overly simplistic "
        "language for the audience, inconsistent terminology across slides\n"
        "- flow: Ideas presented out of logical order, missing context or setup, "
        "jumping between unrelated topics, conclusions before evidence, "
        "unclear cause-and-effect relationships\n"
        "- transitions: Topic connections between slides — abrupt topic changes, "
        "no connecting phrases linking to the previous or next slide, "
        "missing summary/recap before new topics, "
        "no signposting (e.g., 'Next, let's look at...', 'Building on that...'). "
        "This is about the narrative text flow, NOT visual slide transitions.\n\n"
        "IMPORTANT: Do NOT check or penalize note length. Notes can be any length — "
        "short or long is fine. Focus only on grammar, tone, flow, and transitions.\n\n"
        "Be specific. Don't say 'minor issues' — quote the exact problem.\n"
        "Only flag REAL issues. If a slide's notes are well-written, say \"None\" for that category.\n\n"
        "SCORING RUBRIC for the overall score (1-10):\n"
        "- 10: Perfect — no issues at all across all slides\n"
        "- 9: Excellent — 1-2 very minor issues (e.g. a single typo)\n"
        "- 8: Very good — a few minor issues that don't affect readability\n"
        "- 7: Good — some issues worth fixing but notes are solid overall\n"
        "- 5-6: Needs work — consistent issues across multiple slides\n"
        "- 1-4: Poor — widespread grammar errors, incoherent flow, or very wrong tone\n"
        "Well-written professional notes with no real issues should score 9-10. "
        "Do not deduct points for style preferences — only for genuine errors.\n\n"
        f"{notes_block}"
    )
    return prompt


def parse_qa_json(raw: str) -> dict:
    """SlideStudio's ``_parse_qa_json``: a direct parse, else the first fenced
    block, else the outermost braces; {} when none of them is JSON."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def issue_text(value) -> str:
    """A criterion's text as stored: ``None`` for anything in the no-issue set."""
    if value is None:
        return NO_ISSUE_TEXT
    text = (value if isinstance(value, str) else str(value)).strip()
    return NO_ISSUE_TEXT if text.lower() in NO_ISSUE else text[:MAX_ISSUE_CHARS]


def is_issue(text) -> bool:
    return isinstance(text, str) and text.strip().lower() not in NO_ISSUE


def qa_score(raw) -> int | None:
    if isinstance(raw, bool):
        return None
    try:
        return max(1, min(10, int(float(raw))))
    except (TypeError, ValueError):
        return None


def count_issues(entries: list[dict]) -> int:
    """The open issues: a criterion with text that is not fixed yet."""
    return sum(
        1 for entry in entries for criterion in QA_CRITERIA
        if is_issue(entry.get(criterion)) and criterion not in (entry.get("fixed") or [])
    )


def _slide_number(value) -> int | None:
    """The ``"slide": n`` an entry carries, as an int; None when absent or not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def map_qa(parsed: dict, chunk: list[tuple[int, str]]) -> tuple[int | None, list[dict]]:
    """The model's ``slides`` array mapped onto ``chunk``: refused when the
    lengths differ; by the ``"slide": n`` numbers the prompt demands when
    every entry carries one (refused when they are not exactly the slides
    sent - a dropped-and-duplicated or shuffled array would pin issues on the
    wrong slides, and a Fix would then rewrite the wrong notes); positionally
    only when the numbers are absent or not numeric (porting spec, trap 7).
    The old dict shape is read by its values in order, as SlideStudio did."""
    data = parsed.get("slides") if isinstance(parsed, dict) else None
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list):
        raise AiError("The QA review did not come back as the expected JSON (no 'slides' array); nothing was saved. Try again.")
    if len(data) != len(chunk):
        raise AiError(
            f"The QA review returned {len(data)} entries for {len(chunk)} slides, so the issues could not be "
            "matched to slides; nothing was saved. Try again."
        )
    items = [item if isinstance(item, dict) else {} for item in data]
    numbers = [_slide_number(item.get("slide")) for item in items]
    expected = [index + 1 for index, _notes in chunk]
    if all(n is not None for n in numbers):
        if sorted(numbers) != sorted(expected):
            raise AiError(
                f"The QA review's slide numbers ({', '.join(str(n) for n in numbers)}) are not the slides sent "
                f"({', '.join(str(n) for n in expected)}), so the issues could not be matched to slides; nothing was "
                "saved. Try again."
            )
        by_number = dict(zip(numbers, items))
        items = [by_number[n] for n in expected]
    entries = []
    for (index, _notes), item in zip(chunk, items):
        entries.append({"index": index, **{c: issue_text(item.get(c)) for c in QA_CRITERIA}, "fixed": []})
    return qa_score(parsed.get("score")), entries


def _set_qa_review(pid: str, review: dict) -> None:
    with slides.project_lock(pid):
        current = store.get_project(pid)
        if current is None:
            raise slides.ProjectNotFound("Project not found.")
        current["qa_review"] = review
        store.save_project(current)


def _mark_fixed(pid: str, index: int, criterion: str) -> dict | None:
    """Note a criterion as fixed on the saved review (None when the review
    does not cover that slide)."""
    with slides.project_lock(pid):
        current = store.get_project(pid)
        review = (current or {}).get("qa_review")
        if not isinstance(review, dict):
            return None
        for entry in review.get("slides") or []:
            if entry.get("index") == index:
                fixed = entry.setdefault("fixed", [])
                if criterion not in fixed:
                    fixed.append(criterion)
                review["issues"] = count_issues(review.get("slides") or [])
                store.save_project(current)
                return review
        return None


def plan_qa(pid: str):
    """The ``ai-qa`` job: every slide with notes reviewed for grammar, tone,
    flow and transitions in passes of at most ``QA_CHUNK_SIZE`` slides and
    ``QA_CHUNK_CHARS`` of notes, the result saved as ``qa_review`` on the
    project once every pass is in - the previous review stays until then, so
    a pass whose answer cannot be mapped (the job's error) or a cancel keeps
    the last verdict."""
    if not _with_notes(pid):
        raise ValueError("There are no speaker notes to review yet.")
    _require_reachable()

    def work(progress):
        reviewed = [(s["index"], s["speaker_notes"]) for s in slides.list_slides(pid) if (s["speaker_notes"] or "").strip()]
        passes = chunked(reviewed, QA_CHUNK_SIZE, QA_CHUNK_CHARS, text=lambda item: item[1])
        _warm_up(progress)
        entries, scores = [], []
        for n, chunk in enumerate(passes):
            if jobs.cancel_requested_here():
                progress(n / len(passes), f"QA review: cancelled after {n} of {len(passes)} passes")
                return {"cancelled": True, "passes": n, "total_passes": len(passes), "slides": len(reviewed), "saved": False}
            progress(n / len(passes), f"QA review: pass {n + 1}/{len(passes)} ({len(chunk)} slides)…")
            raw = _call(qa_prompt(chunk), timeout=QA_TIMEOUT, system=QA_SYSTEM)
            if not raw.strip():
                raise AiError("The QA review returned nothing; nothing was saved. Try again.")
            score, chunk_entries = map_qa(parse_qa_json(raw), chunk)
            entries.extend(chunk_entries)
            if score is not None:
                scores.append(score)
        issues = count_issues(entries)
        score = round(mean(scores)) if scores else (10 if issues == 0 else 7)
        review = {"score": score, "run_at": _now(), "model": model(), "issues": issues, "slides": entries}
        _set_qa_review(pid, review)
        progress(1.0, f"QA score {score}/10 — {issues} issue(s)")
        return {"score": score, "issues": issues, "slides": len(entries), "passes": len(passes), "cancelled": False, "saved": True}

    return work


def plan_tone(pid: str, tone: str, custom_prompt: str | None = None):
    """The ``ai-tone`` job: every note rewritten for ``tone`` (a
    ``core.tone_adapter`` preset, or ``Custom`` with ``custom_prompt``), one
    ``_call`` per slide with the engine's own prompt."""
    presets = tone_adapter.get_available_tones()
    instruction = (custom_prompt or "").strip()
    if tone == CUSTOM_TONE:
        if not instruction:
            raise ValueError("Enter an instruction for the Custom tone.")
    elif tone not in presets:
        raise ValueError(f"Unknown tone '{tone}'. Choose one of: {', '.join(presets)}, or {CUSTOM_TONE}.")
    indexes = _with_notes(pid)
    if not indexes:
        raise ValueError("There are no speaker notes to adapt yet.")
    _require_reachable()

    def rewrite(note: str) -> str:
        return _call(tone_prompt(note, tone, instruction), system=NO_SYSTEM)

    return lambda progress: _rewrite_loop(pid, indexes, label=f"Tone ({tone})", progress=progress, rewrite=rewrite)


def suggest_voice(subtag: str, provider_id: str) -> dict | None:
    """The first voice of ``provider_id`` whose locale's language is
    ``subtag`` (``core.tts_provider.suggest_voice_for_language`` over the
    provider's listed voices), or None when it has none for the language."""
    entries = voice_lists.voices_for(provider_id).get("voices") or []
    voices = [Voice(voice_id=v["voice_id"], name=v["name"], category=v.get("locale") or "") for v in entries]
    voice_id = suggest_voice_for_language(subtag, voices)
    if not voice_id:
        return None
    return next((v for v in entries if v["voice_id"] == voice_id), None)


def plan_translate(pid: str, language: str, *, match_voice: bool = True, provider: str | None = None):
    """The ``ai-translate`` job: every note replaced in place by its
    translation (the engine's prompt, bracketed markup kept), one ``_call``
    per slide. With ``match_voice`` the result carries ``suggested_voice_id``
    / ``_name``: the first voice of ``provider`` (None = the configured one)
    whose locale is the language's subtag, as SlideStudio's
    ``_switch_to_language_voice`` picked it; null when the provider has none
    for the language."""
    languages = translator.get_available_languages()
    if language not in languages:
        raise ValueError(f"Unknown language '{language}'. Choose one of: {', '.join(languages)}.")
    provider_id = studio_settings.resolve_provider(provider)
    indexes = _with_notes(pid)
    if not indexes:
        raise ValueError("There are no speaker notes to translate yet.")
    _require_reachable()
    subtag = languages[language]

    def rewrite(note: str) -> str:
        return _call(translate_prompt(note, language), system=NO_SYSTEM)

    def work(progress):
        result = _rewrite_loop(pid, indexes, label=f"Translate ({language})", progress=progress, rewrite=rewrite)
        result.update({
            "language": language, "subtag": subtag, "provider": provider_id, "match_voice": match_voice,
            "suggested_voice_id": None, "suggested_voice_name": None,
        })
        if match_voice and not result["cancelled"]:
            voice = suggest_voice(subtag, provider_id)
            if voice:
                result["suggested_voice_id"] = voice["voice_id"]
                result["suggested_voice_name"] = voice["name"]
        return result

    return work


def pacing_rules(pid: str) -> dict:
    """``core.auto_pacing``'s rules over every note, applied now (no model, no
    job): pauses after transition phrases, before statistics, around
    questions. A rules edit is nobody's AI rewrite, so ``ai_enhanced`` is
    left alone. Returns the tally plus the number of adjustments made."""
    listed = slides.list_slides(pid)
    analysed = auto_pacing.analyze_pacing([s["speaker_notes"] or "" for s in listed])
    result = _tally(len(listed), adjustments=0)
    for entry in analysed:
        if not entry["original"].strip():
            result["skipped"] += 1
            continue
        if not entry["changes"] or entry["paced"] == entry["original"]:
            result["unchanged"] += 1
            continue
        slides.update_slide(pid, entry["index"], speaker_notes=entry["paced"])
        result["done"] += 1
        result["adjustments"] += len(entry["changes"])
    return result


def plan_pacing_ai(pid: str):
    """The ``ai-pacing`` job: ``core.auto_pacing.ai_pacing``'s prompt per
    slide through ``_call``; an answer that lost words (shorter than 80 % of
    the note) is replaced by the rules' pacing, as the engine does."""
    indexes = _with_notes(pid)
    if not indexes:
        raise ValueError("There are no speaker notes to pace yet.")
    _require_reachable()

    def rewrite(note: str) -> str:
        answer = _call(pacing_prompt(note), system=NO_SYSTEM).strip()
        if answer and len(answer) >= len(note) * PACING_MIN_RATIO:
            return answer
        return auto_pacing._apply_pacing_rules(note)[0]

    return lambda progress: _rewrite_loop(pid, indexes, label="Pacing (AI)", progress=progress, rewrite=rewrite)


def _export_target(pid: str, record: dict, suffix: str) -> Path:
    """``<project>/exports/<name><suffix>``, proven to lie under the project
    (the name comes from the record; a tampered one must not write outside)."""
    base = (store.PROJECTS_DIR / pid).resolve()
    target = (base / "exports" / f"{record['name']}{suffix}").resolve()
    if base not in target.parents:
        raise ValueError("The project name is not a valid file name.")
    return target


def plan_qa_doc(pid: str, num_questions: int = 10):
    """The ``ai-qa-doc`` job: anticipated audience questions with answers from
    the notes (``core.qa_generator``), written to ``exports/<name>-qa.txt``
    and recorded as ``outputs.qa_doc`` for ``GET /export/qa``. One prompt for
    the whole deck: nothing to cancel between."""
    if isinstance(num_questions, bool) or not isinstance(num_questions, int) or not 1 <= num_questions <= 50:
        raise ValueError("Ask for between 1 and 50 questions.")
    record = slides.project_record(pid)
    if not _with_notes(pid):
        raise ValueError("There are no speaker notes to build questions from yet.")
    target = _export_target(pid, record, "-qa.txt")
    relative = f"exports/{target.name}"
    _require_reachable()

    def work(progress):
        notes = [s["speaker_notes"] or "" for s in slides.list_slides(pid)]
        _warm_up(progress)
        pairs = qa_generator.generate_qa(notes, base_url(), model(), num_questions=num_questions, on_progress=progress)
        if not pairs:
            raise AiError("The model returned no question/answer pairs; try again, or pick another model in Settings › Studio.")
        qa_generator.export_qa_document(pairs, target, title=record["name"])
        with slides.project_lock(pid):
            current = store.get_project(pid) or record
            outputs = dict(current.get("outputs") or {})
            outputs["qa_doc"] = relative
            current["outputs"] = outputs
            store.save_project(current)
        progress(1.0, f"Q&A document ready: {len(pairs)} questions")
        return {"pairs": len(pairs), "qa_doc": relative, "download": f"/api/projects/{pid}/export/qa"}

    return work


def qa_doc_path(pid: str) -> Path | None:
    """The written Q&A document, proven to lie under the project; None when
    there is none (or the record points outside the project)."""
    record = slides.project_record(pid)
    relative = (record.get("outputs") or {}).get("qa_doc")
    if not relative:
        return None
    base = (store.PROJECTS_DIR / pid).resolve()
    path = (base / relative).resolve()
    if base not in path.parents or not path.is_file():
        return None
    return path


# -- the sync operations ---------------------------------------------------------

def enhance_one(pid: str, index: int, *, use_vision: bool = False, notes: str | None = None) -> dict:
    """One slide's AI rewrite as a PROPOSAL: nothing is saved - the editor
    shows it as a draft with Revert. ``notes`` is the text the editor holds
    (an unsaved draft included, as SlideStudio read the editor); None means
    the saved notes."""
    sources = slide_sources(pid)
    src = sources[_check_index(index, len(sources))]
    current = (notes if notes is not None else src.notes).strip()
    vision, reason = vision_for_project(pid) if use_vision else (False, None)
    image = src.image if vision else None
    if not current and not src.title and not src.body and not image:
        raise ValueError("No notes or slide content to work with.")
    prompt = build_prompt(src.title, src.body, current, image=bool(image), mode="enhance")
    text = _call(prompt, images=[str(image)] if image else None).strip()
    if not text:
        raise AiError("The model returned nothing.")
    return {"suggestion": text, "vision": bool(image), "vision_reason": reason if use_vision and not vision else None}


def qa_fix(pid: str, index: int, criterion: str, issue: str) -> dict:
    """Apply one targeted fix (the criterion's constraint from SlideStudio's
    map, the issue text) to the slide's SAVED notes, save it, and mark the
    criterion fixed on the review when the review covers the slide. Returns
    the slide."""
    if criterion not in QA_CRITERIA:
        raise ValueError(f"Unknown QA criterion '{criterion}'. Choose one of: {', '.join(QA_CRITERIA)}.")
    listed = slides.list_slides(pid)
    notes = listed[_check_index(index, len(listed))]["speaker_notes"] or ""
    if not notes.strip():
        raise ValueError(f"Slide {index + 1} has no notes to fix.")
    prompt = (
        f"QA Review found this issue with Slide {index + 1}:\n"
        f"Category: {QA_LABELS[criterion]}\n"
        f"Issue: {(issue or '').strip()}\n\n"
        f"Current speaker notes:\n{notes}\n\n"
        f"{QA_CONSTRAINTS[criterion]}\n"
        "Return ONLY the improved notes text, nothing else."
    )
    text = _call(prompt, system=config.ollama_system_prompt or QA_FIX_SYSTEM).strip()
    if not text:
        raise AiError(f"The model returned nothing for slide {index + 1}; the notes were left as they are.")
    updated = slides.update_slide(pid, index, speaker_notes=text, ai_enhanced=True)
    _mark_fixed(pid, index, criterion)
    return updated


def _pdf_slide_data(source: Path) -> list[dict]:
    """``core.slide_analyzer.extract_slide_data`` for a PDF: the page text and
    whether the page carries an image, per page."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(source))
    except Exception as exc:
        logger.warning("The PDF %s could not be read for analysis: %s", source.name, exc)
        raise ValueError("The PDF could not be read; the server log has the reason.") from exc
    data = []
    try:
        for i, page in enumerate(doc):
            text = page.get_text().strip()
            data.append({
                "index": i, "text": text, "notes": "", "has_images": bool(page.get_images()),
                "word_count": len(text.split()) if text else 0, "notes_word_count": 0,
            })
    finally:
        doc.close()
    return data


def analyze(pid: str) -> dict:
    """``core.slide_analyzer.analyze_slide_content`` over the deck (or the
    PDF's pages) with the EDITOR's notes overlaid - they are what narrates,
    not the file's own. The rules run always; the model's prose suggestions
    only when Ollama answers (rules only otherwise, and ``ai`` says which)."""
    record = slides.project_record(pid)
    source = slides.source_path(pid)
    if record["kind"] == "deck":
        try:
            data = slide_analyzer.extract_slide_data(source)
        except Exception as exc:
            logger.warning("The deck %s could not be read for analysis: %s", source.name, exc)
            raise ValueError("The deck could not be read; the server log has the reason.") from exc
    else:
        data = _pdf_slide_data(source)
    notes = {s["index"]: s["speaker_notes"] or "" for s in slides.list_slides(pid)}
    for item in data:
        text = notes.get(item["index"], item.get("notes") or "")
        item["notes"] = text
        item["notes_word_count"] = len(text.split()) if text.strip() else 0
    online = reachable()
    result = slide_analyzer.analyze_slide_content(
        data, ollama_url=base_url() if online else "", ollama_model=model() if online else "",
    )
    result["ai"] = online
    result["model"] = model() if online else None
    return result
