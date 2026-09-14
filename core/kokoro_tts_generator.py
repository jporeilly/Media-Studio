"""Kokoro TTS integration — local, offline text-to-speech via kokoro-onnx.

A second, selectable TTS provider alongside Edge TTS. Runs fully locally with
ONNX Runtime (no API key, no per-request network). Model weights and voice
embeddings are downloaded once into ``<assets>/models/kokoro/`` (or a config
override path) and cached on disk; a loaded ``Kokoro`` model is cached in
memory per (model, voices) path pair.

For tests/dev, if ``_kokoro_test/kokoro-v1.0.onnx`` exists at the repo root it
is preferred so the already-downloaded files are reused and the network is
never hit.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from utils.helpers import get_cache_path
from utils.logger import get_logger
from core.tts_provider import TTSProvider, OnsetProfile, KOKORO_ONSET, Voice

logger = get_logger("KOKORO_TTS")

# Canonical model filenames published by the kokoro-onnx release.
_MODEL_FILE = "kokoro-v1.0.onnx"
_VOICES_FILE = "voices-v1.0.bin"
_RELEASE_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)
_DOWNLOAD_URLS = {
    _MODEL_FILE: f"{_RELEASE_BASE}/{_MODEL_FILE}",
    _VOICES_FILE: f"{_RELEASE_BASE}/{_VOICES_FILE}",
}

# Kokoro emits 24kHz float32 mono audio.
_KOKORO_RATE = 24000

# In-memory model cache, keyed by (model_path, voices_path).
_MODEL_CACHE: Dict[Tuple[str, str], object] = {}
# Guards the check-and-populate of _MODEL_CACHE so two worker threads on first
# concurrent use load the model once (generation runs in a ThreadPoolExecutor).
_MODEL_CACHE_LOCK = threading.Lock()
_espeak_configured = False

# Curated fallback list of common Kokoro v1.0 voices (used only if the model's
# own get_voices() is unavailable). Prefix encodes accent/gender.
_FALLBACK_VOICES = [
    "af_heart", "af_bella", "af_sarah", "af_nicole", "af_sky",
    "am_michael", "am_adam", "am_echo",
    "bf_emma", "bf_isabella",
    "bm_george", "bm_lewis",
]

# Human-readable category by voice-id prefix.
_CATEGORY_BY_PREFIX = {
    "af": "American English (Female)",
    "am": "American English (Male)",
    "bf": "British English (Female)",
    "bm": "British English (Male)",
}


def _repo_root() -> Path:
    """Return the package/repo root directory (parent of ``core/``)."""
    return Path(__file__).resolve().parent.parent


def _default_models_dir() -> Path:
    """Default on-disk location for downloaded Kokoro model files."""
    # ASSETS_DIR lives in gui/styles.py and points at <repo>/assets.
    from services.styles import ASSETS_DIR
    return ASSETS_DIR / "models" / "kokoro"


def _category_for(voice_id: str) -> str:
    """Map a voice id to a readable category via its 2-char prefix."""
    return _CATEGORY_BY_PREFIX.get(voice_id[:2], "Kokoro")


def _display_name_for(voice_id: str) -> str:
    """Build a readable display name from a voice id (e.g. 'af_heart' -> 'Heart (American English, Female)')."""
    prefix = voice_id[:2]
    base = voice_id.split("_", 1)[-1].replace("_", " ").title() if "_" in voice_id else voice_id
    category = _CATEGORY_BY_PREFIX.get(prefix)
    return f"{base} ({category})" if category else base


def _configure_espeak() -> None:
    """Point phonemizer at the bundled espeak-ng (idempotent, best-effort)."""
    global _espeak_configured
    if _espeak_configured:
        return
    try:
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
        _espeak_configured = True
    except Exception as e:  # pragma: no cover - environment specific
        logger.warning("Could not configure espeak-ng for Kokoro: %s", e)


def _download_file(url: str, dest: Path, progress: Optional[Callable[[float, str], None]] = None) -> None:
    """Download ``url`` to ``dest`` robustly (temp file + atomic rename).

    Reports progress via ``progress(fraction, message)`` when supplied.
    Raises on failure (caller is responsible for handling/logging).
    """
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mktemp(suffix=".part", dir=str(dest.parent)))
    try:
        with urllib.request.urlopen(url) as resp:  # nosec - fixed GitHub release URL
            total = int(resp.headers.get("Content-Length", 0) or 0)
            read = 0
            chunk_size = 1 << 20  # 1 MiB
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    read += len(chunk)
                    if progress and total > 0:
                        progress(read / total, f"Downloading {dest.name}: {read // (1<<20)}MB")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def ensure_kokoro_model(
    progress: Optional[Callable[[float, str], None]] = None,
) -> Tuple[Path, Path]:
    """Resolve (and download if needed) the Kokoro model and voices files.

    Resolution order:
      1. ``_kokoro_test/<file>`` at the repo root, if present (dev/test reuse —
         never hits the network).
      2. ``config.kokoro_model_dir`` override, if set.
      3. The built-in models dir ``<assets>/models/kokoro/``; missing files are
         downloaded from the kokoro-onnx GitHub release.

    Args:
        progress: Optional ``progress(fraction, message)`` callback for downloads.

    Returns:
        ``(model_path, voices_path)``.

    Raises:
        RuntimeError: If files cannot be resolved or downloaded.
    """
    # 1. Dev/test fallback: reuse already-downloaded files, no network.
    test_dir = _repo_root() / "_kokoro_test"
    test_model = test_dir / _MODEL_FILE
    test_voices = test_dir / _VOICES_FILE
    if test_model.exists() and test_voices.exists():
        return test_model, test_voices

    # 2. Config override directory.
    from utils.config import config
    override = (config.kokoro_model_dir or "").strip()
    models_dir = Path(override) if override else _default_models_dir()

    model_path = models_dir / _MODEL_FILE
    voices_path = models_dir / _VOICES_FILE

    # 3. Download any missing files.
    for path, name in ((model_path, _MODEL_FILE), (voices_path, _VOICES_FILE)):
        if not path.exists():
            logger.info("Kokoro model file missing, downloading: %s", name)
            try:
                _download_file(_DOWNLOAD_URLS[name], path, progress)
            except Exception as e:
                raise RuntimeError(f"Failed to download Kokoro file {name}: {e}") from e

    return model_path, voices_path


def kokoro_model_present() -> bool:
    """Return True iff a complete Kokoro model pair already exists on disk.

    Stat-only check that mirrors :func:`ensure_kokoro_model`'s resolution order
    (``_kokoro_test/`` → ``config.kokoro_model_dir`` → default assets dir) but
    NEVER downloads, touches the network, or has any side effects. Safe to call
    on the UI build path to decide whether the model still needs downloading.

    A "complete pair" means both the model ``.onnx`` and the ``voices .bin``
    file exist in the same candidate directory.
    """
    candidates = [_repo_root() / "_kokoro_test"]

    try:
        from utils.config import config
        override = (config.kokoro_model_dir or "").strip()
        if override:
            candidates.append(Path(override))
    except Exception:  # pragma: no cover - config import/access guard
        pass

    candidates.append(_default_models_dir())

    for directory in candidates:
        if (directory / _MODEL_FILE).exists() and (directory / _VOICES_FILE).exists():
            return True
    return False


def _load_model(model_path: Path, voices_path: Path):
    """Load (and cache) a ``Kokoro`` model for the given file pair.

    Uses double-checked locking so concurrent first-use callers (generation runs
    in a ``ThreadPoolExecutor``) load the model exactly once.
    """
    key = (str(model_path), str(voices_path))
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached

    with _MODEL_CACHE_LOCK:
        # Re-check inside the lock — another thread may have populated it.
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached

        _configure_espeak()
        from kokoro_onnx import Kokoro
        model = Kokoro(str(model_path), str(voices_path))
        _MODEL_CACHE[key] = model
        return model


class KokoroTTSGenerator(TTSProvider):
    """Generates audio using Kokoro (kokoro-onnx) — local, offline, free."""

    provider_id = "kokoro"

    def __init__(self):
        self._voices: List[Voice] = []

    def onset_profile(self) -> OnsetProfile:
        """Kokoro is quieter than Edge — use the relative-threshold profile."""
        return KOKORO_ONSET

    def test_api_key(self) -> tuple:
        """No API key — report whether the local model files are present.

        Uses :func:`kokoro_model_present` (stat-only), so this never triggers a
        download: it reports availability, it does not cause it.
        """
        if kokoro_model_present():
            return True, "Kokoro: local, no API key"
        return False, "Kokoro model not downloaded"

    @staticmethod
    def curated_voices() -> List[Voice]:
        """Return the offline curated voice list — never loads the model.

        Safe for the UI build path: builds :class:`Voice` objects purely from the
        in-memory ``_FALLBACK_VOICES`` constant with no disk or network access.
        """
        return [
            Voice(
                voice_id=name,
                name=_display_name_for(name),
                category=_category_for(name),
                description=f"Kokoro voice {name}",
            )
            for name in _FALLBACK_VOICES
        ]

    def model_voices(self) -> List[Voice]:
        """The voices the Kokoro model on disk actually carries - strict.

        Loads the model (never downloads it: an absent model is an error here,
        not a trigger) and lets a load failure propagate, so a model that is
        present but broken is reported rather than papered over with the
        curated list. Raises ``RuntimeError`` when the model is absent or lists
        no voices. :meth:`fetch_voices` is the forgiving wrapper.
        """
        if not kokoro_model_present():
            raise RuntimeError("Kokoro model not downloaded")
        model_path, voices_path = ensure_kokoro_model()
        model = _load_model(model_path, voices_path)
        names = sorted(str(n) for n in (model.get_voices() or []))
        if not names:
            raise RuntimeError("The Kokoro model lists no voices")
        self._voices = [
            Voice(
                voice_id=name,
                name=_display_name_for(name),
                category=_category_for(name),
                description=f"Kokoro voice {name}",
            )
            for name in names
        ]
        return self._voices

    def fetch_voices(self) -> List[Voice]:
        """Return available Kokoro voices (no network required) - never raises.

        Uses the model's own list (:meth:`model_voices`) when the model is
        already present on disk; otherwise, or when the model will not load,
        falls back to the curated list of common voices. The model is only
        loaded when ``kokoro_model_present()`` is True, so this never triggers
        a download.
        """
        if kokoro_model_present():
            try:
                return self.model_voices()
            except Exception as e:
                logger.warning("Falling back to curated Kokoro voice list: %s", e)

        self._voices = self.curated_voices()
        return self._voices

    @property
    def voices(self) -> List[Voice]:
        return self._voices

    def generate_audio(
        self,
        text: str,
        voice_id: str,
        output_path: Optional[Path] = None,
        speed: float = 1.0,
        use_cache: bool = True,
        # Accepted for call-site compatibility but ignored by Kokoro.
        model_id: str = "",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
    ) -> Optional[Path]:
        """Generate audio from ``text`` using Kokoro.

        Args:
            text: Text to convert to speech.
            voice_id: Kokoro voice name (e.g. "af_heart"). Empty/None falls
                back to ``config.kokoro_voice``.
            output_path: Where to save the audio (.mp3 or .wav). When None a
                cache path is used.
            speed: Speech speed (0.5-2.0, default 1.0).
            use_cache: Whether to use/check the on-disk cache.
            model_id, stability, similarity_boost, style: Ignored (call-site compat).

        Returns:
            Path to the generated audio file, or None on failure.
        """
        if not text.strip():
            return None

        from utils.config import config as _cfg

        # Resolve voice + language from config defaults when not supplied.
        if not voice_id:
            voice_id = _cfg.kokoro_voice
        lang = _cfg.kokoro_lang or "en-us"

        # Strip SSML-like markup if enabled (mirror Edge behaviour).
        if _cfg.ssml_enabled:
            from utils.ssml_parser import has_markup, strip_markup
            if has_markup(text):
                text = strip_markup(text)

        # Fold the active language into the cache key so the same
        # text+voice+speed in a different language does not return a stale clip.
        cache_voice_key = f"{voice_id}|{lang}"

        cache_path = get_cache_path(
            text, cache_voice_key, speed=speed,
            stability=0, similarity_boost=0, style=0,
        ) if use_cache else None

        # Cache hit.
        if cache_path and cache_path.exists():
            if output_path:
                shutil.copy(cache_path, output_path)
                return output_path
            return cache_path

        if output_path is None:
            output_path = cache_path or get_cache_path(
                text, cache_voice_key, speed=speed,
                stability=0, similarity_boost=0, style=0,
            )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            model_path, voices_path = ensure_kokoro_model()
            model = _load_model(model_path, voices_path)
            samples, rate = model.create(text, voice=voice_id, speed=speed, lang=lang)

            self._write_audio(samples, rate, output_path)

            # Mirror to cache if we generated to a different path.
            if cache_path and cache_path != output_path and output_path.exists():
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(output_path, cache_path)

            return output_path if output_path.exists() else None

        except Exception as e:
            logger.error("Error generating Kokoro audio: %s", e)
            return None

    @staticmethod
    def _write_audio(samples, rate: int, output_path: Path) -> None:
        """Write float32 samples to ``output_path`` as .mp3 or .wav."""
        import numpy as np

        suffix = output_path.suffix.lower()
        if suffix == ".wav":
            import soundfile as sf
            sf.write(str(output_path), samples, rate)
            return

        # Default (and the pipeline's format) is mp3: float32 [-1,1] -> int16 PCM.
        from pydub import AudioSegment
        arr = np.asarray(samples, dtype=np.float32)
        arr = np.clip(arr, -1.0, 1.0)
        pcm16 = (arr * 32767.0).astype(np.int16)
        segment = AudioSegment(
            pcm16.tobytes(),
            frame_rate=int(rate),
            sample_width=2,
            channels=1,
        )
        segment.export(str(output_path), format="mp3")

    def get_remaining_characters(self) -> Optional[int]:
        """Kokoro runs locally — effectively unlimited."""
        return 999_999_999
