"""Speaker-note translation via a local Ollama model.

Replaces the earlier Argos Translate backend, which dragged in ``torch`` +
``stanza`` (~500 MB) for lower-quality offline translation and was never wired
into the pipeline. Translation now uses the same local Ollama server already
configured for notes enhancement / QA, so there is no extra heavyweight
dependency and quality tracks whatever model has been pulled.

Pure module: no NiceGUI imports. Wire it from the generation-action layer, and
pair the chosen language with a matching TTS voice via
``core.tts_provider.suggest_voice_for_language``.
"""

from typing import List, Optional, Callable
import logging

logger = logging.getLogger("mediastudio.TRANSLATE")

# Curated target languages: display name -> BCP-47 language subtag. The subtag
# is what pairs the translation with a matching TTS voice (voices carry a locale
# like "fr-FR" whose language part is this subtag).
LANGUAGES = {
    "Spanish": "es",
    "French": "fr",
    "German": "de",
    "Italian": "it",
    "Portuguese": "pt",
    "Dutch": "nl",
    "Polish": "pl",
    "Russian": "ru",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese (Simplified)": "zh",
    "Arabic": "ar",
    "Hindi": "hi",
    "English": "en",
}


def get_available_languages() -> dict:
    """Return the supported target languages as ``{display name: subtag}``."""
    return dict(LANGUAGES)


def _has_markup(text: str) -> bool:
    """True when the note contains bracketed SSML-style markup to preserve."""
    return "[" in text and "]" in text


def translate_text(
    text: str,
    target_language: str,
    ollama_url: str,
    ollama_model: str,
) -> str:
    """Translate a single string into ``target_language`` via Ollama.

    Bracketed markup ([pause:1s], [break], [emphasis]) is preserved verbatim and
    in place. Returns the original text unchanged on empty input or any failure,
    so a translation error never drops content.
    """
    import requests

    if not text.strip():
        return text

    guard = (
        " Keep any bracketed markup such as [pause:1s], [break] or [emphasis] "
        "exactly as written and in the same position."
        if _has_markup(text)
        else ""
    )
    prompt = (
        f"Translate the text below into {target_language}. "
        f"Output only the translation — no preamble, quotes, or explanation."
        f"{guard}\n\nText:\n{text}\n\nTranslation:"
    )

    try:
        resp = requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": ollama_model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        if resp.status_code == 200:
            result = resp.json().get("response", "").strip()
            if result:
                return result
        else:
            logger.warning("Translation HTTP %s for a note", resp.status_code)
    except Exception as e:
        logger.warning("Translation failed for a note: %s", e)
    return text


def translate_notes(
    notes: List[str],
    target_language: str,
    ollama_url: str,
    ollama_model: str,
    on_progress: Optional[Callable] = None,
) -> List[str]:
    """Translate every speaker note into ``target_language``.

    Args:
        notes: speaker-note strings, one per slide.
        target_language: a display name from :data:`LANGUAGES` (e.g. "French").
        ollama_url: Ollama server URL.
        ollama_model: model to use.
        on_progress: optional ``callback(fraction, message)``.

    Returns a new list the same length as ``notes``; empty notes pass through and
    any note that fails to translate keeps its original text.
    """
    out: List[str] = []
    total = len(notes) or 1
    for i, note in enumerate(notes):
        if on_progress:
            on_progress(i / total, f"Translating slide {i + 1}/{len(notes)} to {target_language}...")
        out.append(translate_text(note, target_language, ollama_url, ollama_model))
    if on_progress:
        on_progress(1.0, f"Translated {len(notes)} slides to {target_language}")
    return out
