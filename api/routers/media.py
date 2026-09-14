"""Media options for the generator: available TTS voices (per provider) and
output presets."""

from fastapi import APIRouter, Depends, HTTPException

from api.deps import current_user
from services import studio_settings, voices as voice_lists
from services.output_presets import list_presets

router = APIRouter(tags=["media"])


@router.get("/voices")
def voices(provider: str | None = None, user: dict = Depends(current_user)):
    """Available narration voices for ``provider`` (edge_tts | kokoro; default =
    the configured provider).

    Never a 500 for a missing service: the payload's ``error`` carries the
    reason and ``voices`` is empty, so the generator UI still loads
    (``services.voices``).
    """
    try:
        provider_id = studio_settings.resolve_provider(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return voice_lists.voices_for(provider_id)


@router.get("/output-presets")
def output_presets(user: dict = Depends(current_user)):
    """The selectable output presets (resolution + bitrate per delivery target)."""
    return {"presets": list_presets()}


@router.get("/languages")
def languages(user: dict = Depends(current_user)):
    """Target languages for re-voice translation (via the local Ollama model)."""
    from core.translator import get_available_languages

    return {
        "languages": [
            {"name": name, "subtag": subtag}
            for name, subtag in get_available_languages().items()
        ]
    }
