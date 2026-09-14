"""The narration voices of each provider, as the API lists them.

One entry per voice: ``{voice_id, name, locale, gender}``. ``locale`` is what
pairs a voice with a language - an Edge voice carries it outright
(``en-US``), a Kokoro voice encodes it in the id's first letter. Lifted out of
``api/routers/media.py`` so the translate job (``services.ai_slides``) can pick
a voice for the target language from the same list the Generate card shows.

Never a raise for a missing service: ``error`` carries the reason and
``voices`` is empty, so the generator UI still loads.
"""

import re

from utils.config import config

# A Kokoro voice id encodes its accent and gender in the prefix: "af_heart" is
# American English, female; "bm_george" British English, male.
_KOKORO_LOCALES = {
    "a": "en-US", "b": "en-GB", "e": "es-ES", "f": "fr-FR", "h": "hi-IN",
    "i": "it-IT", "j": "ja-JP", "p": "pt-BR", "z": "zh-CN",
}
_KOKORO_GENDERS = {"f": "Female", "m": "Male"}

# Edge display names end in "(en-US, Female)".
_EDGE_GENDER = re.compile(r"\([^()]*,\s*(Female|Male|Neutral)\)\s*$")

KOKORO_MODEL_NOTICE = (
    "Kokoro's model is not downloaded yet — it downloads once (about 340 MB) on the "
    "first Kokoro narration. This is the built-in voice list."
)


def _payload(provider: str, voices: list, error: str | None = None, notice: str | None = None) -> dict:
    return {"provider": provider, "voices": voices, "error": error, "notice": notice}


def _edge_entry(voice_id: str, name: str, locale: str) -> dict:
    match = _EDGE_GENDER.search(name or "")
    return {"voice_id": voice_id, "name": name, "locale": locale, "gender": match.group(1) if match else None}


def _kokoro_entry(voice_id: str, name: str) -> dict:
    return {
        "voice_id": voice_id,
        "name": name,
        "locale": _KOKORO_LOCALES.get(voice_id[:1], ""),
        "gender": _KOKORO_GENDERS.get(voice_id[1:2]),
    }


def edge_voices() -> dict:
    """Edge voices from the config cache (a week), refreshed from Microsoft's
    service when the cache is stale or empty. Offline, a stale cache still
    serves (with a notice); with nothing cached the list is empty and
    ``error`` says so."""
    cached = config.get_cached_edge_voices()
    if cached:
        return _payload("edge_tts", [_edge_entry(v["voice_id"], v["name"], v["category"]) for v in cached])
    try:
        from core.edge_tts_generator import EdgeTTSGenerator

        fetched = EdgeTTSGenerator().fetch_voices()
    except Exception as exc:
        stale = config.get_cached_edge_voices(allow_stale=True)
        if stale:
            return _payload(
                "edge_tts",
                [_edge_entry(v["voice_id"], v["name"], v["category"]) for v in stale],
                notice=f"Showing the last fetched Edge voice list; the refresh failed: {exc}",
            )
        return _payload("edge_tts", [], error=f"Could not fetch the Edge TTS voices (offline?): {exc}")
    if fetched:
        config.cache_edge_voices(fetched)
    return _payload("edge_tts", [_edge_entry(v.voice_id, v.name, v.category) for v in fetched])


def kokoro_voices() -> dict:
    """Kokoro voices without ever downloading the model: the config cache, else
    the model's own list when it is on disk (strict - a model that will not
    load is reported, never cached), else the generator's curated list with a
    notice that the model is still to come. Only real model output is cached.
    When Kokoro cannot be imported at all, the list is empty and ``error`` says why.
    """
    try:
        from core.kokoro_tts_generator import KokoroTTSGenerator, kokoro_model_present

        cached = config.get_cached_kokoro_voices()
        if cached:
            return _payload("kokoro", [_kokoro_entry(v["voice_id"], v["name"]) for v in cached])
        if kokoro_model_present():
            fetched = KokoroTTSGenerator().model_voices()
            config.cache_kokoro_voices(fetched)
            return _payload("kokoro", [_kokoro_entry(v.voice_id, v.name) for v in fetched])
        curated = KokoroTTSGenerator.curated_voices()
    except Exception as exc:
        return _payload("kokoro", [], error=f"Kokoro is not available on this server: {exc}")
    return _payload("kokoro", [_kokoro_entry(v.voice_id, v.name) for v in curated], notice=KOKORO_MODEL_NOTICE)


def voices_for(provider_id: str) -> dict:
    """The voice payload of one provider id (``edge_tts`` | ``kokoro``)."""
    return kokoro_voices() if provider_id == "kokoro" else edge_voices()
