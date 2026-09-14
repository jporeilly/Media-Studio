"""Studio settings: the narration, transcription and output defaults every job
starts from, shared by everyone on the server.

A small typed layer over ``utils.config`` (the engine's JSON-backed config,
``data/config.json``). Admins edit these in Settings › Studio
(``GET``/``PUT /api/settings/studio``); every signed-in user may read them, and
the project pages use them to preselect the narration provider and its voice.

Each setting maps to ONE config key - the key the engine reads:

- ``tts_provider``     ``config.tts_provider`` (``get_tts_provider`` default)
- ``edge_tts_voice``   ``config.edge_tts_voice`` - the Edge default voice, as
  ``core.tts_provider.default_voice_for_provider`` reads it. The legacy
  ``default_voice_id`` key from the first Slide Studio editions is not read
  by anything in this repo and is left alone.
- ``kokoro_voice`` / ``kokoro_lang``  ``config.kokoro_voice`` / ``config.kokoro_lang``
- ``whisper_model``    ``config.whisper_model`` ("" = the recommended default;
  the transcribe route resolves it at request time, see ``resolve_whisper_model``)
- ``ollama_model``     ``config._config["ollama_model"]`` (``services.revoice``)
- ``output_folder``    ``config.output_folder``
- ``transition_pause`` / ``music_volume``  ``config.transition_pause`` / ``config.music_volume``

``update_settings`` validates every change and raises ``ValueError`` with a
message the user can read; the API turns that into HTTP 400.
"""

import math
import os
import uuid
from pathlib import Path

from utils.config import config

PROVIDERS = {
    "edge_tts": {
        "label": "Edge TTS",
        "note": "Microsoft's online voices — free, needs internet.",
    },
    "kokoro": {
        "label": "Kokoro",
        "note": "Local, offline voices — the model (about 340 MB) downloads once, on the first Kokoro narration.",
    },
}

# Kokoro v1.0 languages (the espeak-ng codes Kokoro accepts), as the desktop
# edition's sidebar offered them.
KOKORO_LANGUAGES = {
    "en-us": "English (US)",
    "en-gb": "English (UK)",
    "fr-fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "hi": "Hindi",
    "pt-br": "Portuguese (BR)",
}

# The model ``services.revoice`` translates with when none is configured.
OLLAMA_FALLBACK_MODEL = "llama3"

# Whisper models whose engine description does not already say so.
_WHISPER_GPU_RECOMMENDED = ("distil-large-v3", "large-v3")

TRANSITION_PAUSE_RANGE = {"min": 0.0, "max": 5.0, "step": 0.1, "unit": "seconds"}
MUSIC_VOLUME_RANGE = {"min": 0.0, "max": 1.0, "step": 0.05}

FIELDS = (
    "tts_provider", "edge_tts_voice", "kokoro_voice", "kokoro_lang", "whisper_model",
    "ollama_model", "output_folder", "transition_pause", "music_volume",
)


def _whisper_models() -> dict:
    # Imported lazily: the engine module sets up its CUDA/DLL globals on import
    # and the web process should stay light until a transcription runs.
    from core.video_importer import WHISPER_MODELS
    return WHISPER_MODELS


def ollama_model() -> str:
    """The Ollama model translation runs with: the configured one, else the fallback."""
    return config._config.get("ollama_model") or OLLAMA_FALLBACK_MODEL


# -- read ------------------------------------------------------------------

def get_settings() -> dict:
    """The current values, one entry per field, as the engine will use them."""
    return {
        "tts_provider": config.tts_provider,
        "edge_tts_voice": config.edge_tts_voice,
        "kokoro_voice": config.kokoro_voice,
        "kokoro_lang": config.kokoro_lang,
        "whisper_model": config.whisper_model,
        "ollama_model": ollama_model(),
        "output_folder": config.output_folder,
        "transition_pause": float(config.transition_pause),
        "music_volume": float(config.music_volume),
    }


def describe() -> dict:
    """What the UI renders the form from: the allowed values (with labels and a
    one-line note) for the enumerated fields and the ranges of the numeric ones."""
    whisper = [{
        "value": "",
        "label": "Recommended default",
        "note": "large-v3-turbo on a GPU, medium on the CPU",
    }]
    for name, info in _whisper_models().items():
        note = f"{info['description']} ({info['size']})"
        if name in _WHISPER_GPU_RECOMMENDED:
            note += " — GPU recommended"
        whisper.append({"value": name, "label": name, "note": note})
    return {
        "tts_provider": {
            "options": [{"value": pid, **meta} for pid, meta in PROVIDERS.items()],
        },
        "kokoro_lang": {
            "options": [{"value": code, "label": label} for code, label in KOKORO_LANGUAGES.items()],
        },
        "whisper_model": {"options": whisper},
        "transition_pause": dict(TRANSITION_PAUSE_RANGE),
        "music_volume": dict(MUSIC_VOLUME_RANGE),
    }


# -- narration resolution (used by the generate / re-voice routes) ---------

def resolve_provider(provider: str | None) -> str:
    """A provider id from a request value: None/"" means the configured one."""
    if not provider:
        return config.tts_provider
    if provider not in PROVIDERS:
        raise ValueError(
            f"Unknown narration provider '{provider}'. Choose edge_tts (Edge TTS) or kokoro (Kokoro)."
        )
    return provider


def check_voice_for_provider(voice_id: str, provider: str) -> str:
    """``voice_id`` unless it clearly belongs to the other provider.

    Edge ids look like ``en-US-AriaNeural`` (hyphenated); Kokoro ids like
    ``af_heart`` (two letters, an underscore, a name). Sending one provider's id
    to the other fails deep inside synthesis, so it is refused here with a
    message that says which shape was expected. Anything else is passed on -
    the provider itself decides whether the voice exists.
    """
    voice = (voice_id or "").strip()
    if not voice:
        raise ValueError("Enter a voice id.")
    if provider == "kokoro" and "-" in voice:
        raise ValueError(
            f"'{voice}' looks like an Edge TTS voice, not a Kokoro one (Kokoro voices look like af_heart)."
        )
    if provider != "kokoro" and _looks_like_kokoro(voice):
        raise ValueError(
            f"'{voice}' looks like a Kokoro voice, not an Edge TTS one (Edge voices look like en-US-AriaNeural)."
        )
    return voice


def _looks_like_kokoro(voice: str) -> bool:
    prefix, sep, name = voice.partition("_")
    return sep == "_" and len(prefix) == 2 and prefix.isalpha() and prefix.islower() and bool(name)


def resolve_whisper_model(requested: str | None) -> str:
    """The Whisper model a transcription job runs with: the request's, else the
    studio's (Settings › Studio), else "" for the engine's recommended default.
    Resolved at request time so the job is pinned to the setting as it was.
    Raises ``ValueError`` for a model name the engine does not know.
    """
    return _whisper_model(requested) or config.whisper_model


def resolve_narration(provider: str | None, voice_id: str | None) -> tuple[str, str]:
    """The ``(provider, voice)`` a narration job runs with.

    ``provider`` None/"" means the configured provider; ``voice_id`` None/""
    means that provider's configured default voice (Settings › Studio).
    Raises ``ValueError`` for an unknown provider or a voice from the other one.
    """
    provider_id = resolve_provider(provider)
    if not (voice_id or "").strip():
        # The engine's own reading of the per-provider default (one meaning).
        from core.tts_provider import default_voice_for_provider
        return provider_id, default_voice_for_provider(provider_id)
    return provider_id, check_voice_for_provider(voice_id, provider_id)


# -- write -----------------------------------------------------------------

def _text(value, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(message)
    return value.strip()


def _number(value, bounds: dict, message: str) -> float:
    if isinstance(value, bool):
        raise ValueError(message)
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if not math.isfinite(number) or not bounds["min"] <= number <= bounds["max"]:
        raise ValueError(message)
    return round(number, 3)


def _provider(value) -> str:
    if value not in PROVIDERS:
        raise ValueError(
            f"Unknown narration provider '{value}'. Choose edge_tts (Edge TTS) or kokoro (Kokoro)."
        )
    return value


def _edge_voice(value) -> str:
    return check_voice_for_provider(_text(value, "Enter an Edge TTS voice id (for example en-US-AriaNeural)."), "edge_tts")


def _kokoro_voice(value) -> str:
    return check_voice_for_provider(_text(value, "Enter a Kokoro voice id (for example af_heart)."), "kokoro")


def _kokoro_lang(value) -> str:
    if value not in KOKORO_LANGUAGES:
        raise ValueError(
            f"Unknown Kokoro language '{value}'. Choose one of: {', '.join(KOKORO_LANGUAGES)}."
        )
    return value


def _whisper_model(value) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or value not in _whisper_models():
        raise ValueError(
            f"Unknown Whisper model '{value}'. Choose one of: {', '.join(_whisper_models())}; "
            "or leave it empty for the recommended default."
        )
    return value


def _ollama_model(value) -> str:
    return _text(value, "Enter an Ollama model name (for example gemma3:12b or llama3).")


def _output_folder(value) -> str:
    """The value check only (text, absolute); the filesystem side of it is
    ``_prepare_output_folder``, run once every other value has passed."""
    text = _text(value, "Enter an output folder.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError(
            "The output folder must be an absolute path on the server (for example C:\\MediaStudio\\output)."
        )
    return str(path)


def _prepare_output_folder(text: str) -> str:
    """Create the folder if missing and prove it is writable (a probe file;
    ``os.access`` is unreliable on Windows). Raises ``ValueError`` otherwise."""
    path = Path(text)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"The output folder could not be created: {exc}") from exc
    probe = path / f".write-check-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"The output folder is not writable: {exc}") from exc
    return str(path)


def _transition_pause(value) -> float:
    return _number(value, TRANSITION_PAUSE_RANGE,
                   "The transition pause must be a number of seconds between 0 and 5.")


def _music_volume(value) -> float:
    return _number(value, MUSIC_VOLUME_RANGE,
                   "The music volume must be a number between 0 (silent) and 1 (full).")


_VALIDATORS = {
    "tts_provider": _provider,
    "edge_tts_voice": _edge_voice,
    "kokoro_voice": _kokoro_voice,
    "kokoro_lang": _kokoro_lang,
    "whisper_model": _whisper_model,
    "ollama_model": _ollama_model,
    "output_folder": _output_folder,
    "transition_pause": _transition_pause,
    "music_volume": _music_volume,
}


def update_settings(changes: dict) -> dict:
    """Validate ``changes`` (a partial dict of fields) and persist them.

    Nothing is written unless every change is valid: a bad value raises
    ``ValueError`` with a user-readable message and leaves the config as it was.
    Returns the settings after the change.
    """
    unknown = sorted(set(changes) - set(FIELDS))
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(unknown)}.")
    clean = {key: _VALIDATORS[key](value) for key, value in changes.items()}
    # Filesystem work only after every value has passed, so a request refused
    # for another field leaves no folder behind.
    if "output_folder" in clean:
        clean["output_folder"] = _prepare_output_folder(clean["output_folder"])
    # Written to the backing dict directly and saved once: the Config property
    # setters each rewrite data/config.json, one write per field.
    for key, value in clean.items():
        config._config[key] = value
    if clean:
        config.save()
    return get_settings()
