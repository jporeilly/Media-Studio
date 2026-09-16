"""Edge TTS integration — free text-to-speech using Microsoft Edge voices.

The sole text-to-speech provider for the app. No API key required.
Uses the edge-tts package which provides 400+ voices across 100+ languages.
"""

import asyncio
import shutil
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

from utils.helpers import get_cache_path, publish_to_cache
from utils.logger import get_logger
from core.tts_provider import TTSProvider, OnsetProfile, EDGE_ONSET

logger = get_logger("EDGE_TTS")


def _run_async(coro, timeout: float = 120.0):
    """Run an async coroutine from a sync context (safe in worker threads)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    except asyncio.TimeoutError:
        raise TimeoutError(f"Edge TTS timed out after {timeout}s")
    finally:
        loop.close()


@dataclass
class EdgeVoice:
    """Represents an Edge TTS voice."""
    voice_id: str      # e.g. "en-US-AriaNeural"
    name: str          # e.g. "Aria (en-US, Female)"
    category: str      # e.g. "en-US"
    description: str = ""  # e.g. "Microsoft Aria - Female"


class EdgeTTSGenerator(TTSProvider):
    """Generates audio using Microsoft Edge TTS (free, no API key)."""

    provider_id = "edge_tts"

    def __init__(self):
        self._voices: List[EdgeVoice] = []

    def onset_profile(self) -> OnsetProfile:
        """Edge's loud output uses the fixed -13 dB onset profile."""
        return EDGE_ONSET

    def test_api_key(self) -> tuple:
        """Always succeeds — no API key needed."""
        return True, "Edge TTS: free, no API key required"

    def fetch_voices(self) -> List[EdgeVoice]:
        """Fetch available voices from edge-tts."""
        import edge_tts

        voices_data = _run_async(edge_tts.list_voices())
        self._voices = []
        for v in voices_data:
            short_name = v["ShortName"]       # e.g. "en-US-AriaNeural"
            locale = v["Locale"]              # e.g. "en-US"
            gender = v.get("Gender", "")

            # Build a clean display name: "AriaNeural (en-US, Female)"
            display_name = short_name.split("-", 2)[-1] if "-" in short_name else short_name
            display = f"{display_name} ({locale}, {gender})"

            self._voices.append(EdgeVoice(
                voice_id=short_name,
                name=display,
                category=locale,
                description=f"{v.get('FriendlyName', short_name)} - {gender}",
            ))

        self._voices.sort(key=lambda v: v.name)
        return self._voices

    @property
    def voices(self) -> List[EdgeVoice]:
        return self._voices

    def generate_audio(
        self,
        text: str,
        voice_id: str,
        output_path: Optional[Path] = None,
        speed: float = 1.0,
        use_cache: bool = True,
        # Accepted for call-site compatibility but ignored by Edge TTS
        model_id: str = "",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
    ) -> Optional[Path]:
        """Generate audio from text using Edge TTS.

        Args:
            text: Text to convert to speech.
            voice_id: Edge TTS voice short name (e.g. "en-US-AriaNeural").
            output_path: Where to save the audio file.
            speed: Speech speed (0.5-2.0, default 1.0).
            use_cache: Whether to use/check cache.
            stability, similarity_boost, style: Ignored (call-site compat).

        Returns:
            Path to the generated audio file, or None on failure.
        """
        if not text.strip():
            return None

        # Strip SSML-like markup if enabled
        from utils.config import config as _cfg
        if _cfg.ssml_enabled:
            from utils.ssml_parser import has_markup, strip_markup
            if has_markup(text):
                text = strip_markup(text)

        # Compute cache path
        cache_path = get_cache_path(
            text, voice_id, speed=speed,
            stability=0, similarity_boost=0, style=0,
        ) if use_cache else None

        # Check cache
        if cache_path and cache_path.exists():
            if output_path:
                shutil.copy(cache_path, output_path)
                return output_path
            return cache_path

        # Determine output path
        if output_path is None:
            output_path = cache_path or get_cache_path(
                text, voice_id, speed=speed,
                stability=0, similarity_boost=0, style=0,
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert speed float to edge-tts rate string: 1.0 -> "+0%", 0.85 -> "-15%"
        rate_pct = int((speed - 1.0) * 100)
        rate_str = f"{rate_pct:+d}%"

        try:
            import edge_tts
            communicate = edge_tts.Communicate(text, voice_id, rate=rate_str)
            _run_async(communicate.save(str(output_path)))

            # Also save to cache if different path. Published with an atomic
            # rename, never copied onto the key in the open: an interrupted copy
            # leaves a truncated entry there FOREVER (nothing sweeps data/cache,
            # and the only completeness check anywhere is that the file exists),
            # which every later preview serves and every later re-voice copies
            # out and counts as a successful sentence. publish_to_cache never
            # raises - failing to memoise a clip that was synthesised correctly
            # is not a reason to report that this sentence produced no audio.
            if cache_path and cache_path != output_path and output_path.exists():
                publish_to_cache(output_path, cache_path)

            return output_path if output_path.exists() else None

        except Exception as e:
            logger.error("Error generating audio: %s", e)
            return None

    def get_remaining_characters(self) -> Optional[int]:
        """Edge TTS is free — unlimited characters."""
        return 999_999_999
