"""Media options for the generator: available TTS voices and output presets."""

from fastapi import APIRouter, Depends

from api.deps import current_user
from services.output_presets import list_presets

router = APIRouter(tags=["media"])


@router.get("/voices")
def voices(user: dict = Depends(current_user)):
    """Available TTS voices, listed from the Edge generator.

    The voices are fetched from Microsoft's Edge TTS service over the network;
    if that fetch fails (offline, blocked) an empty list is returned rather than
    a 500 so the generator UI still loads.
    """
    try:
        from core.edge_tts_generator import EdgeTTSGenerator

        generator = EdgeTTSGenerator()
        return {
            "voices": [
                {"voice_id": v.voice_id, "name": v.name, "category": v.category}
                for v in generator.fetch_voices()
            ]
        }
    except Exception:
        return {"voices": []}


@router.get("/output-presets")
def output_presets(user: dict = Depends(current_user)):
    """The selectable output presets (resolution + bitrate per delivery target)."""
    return {"presets": list_presets()}
