"""TTS provider abstraction — common interface for all TTS backends.

The app supports multiple selectable text-to-speech providers (Edge TTS,
Kokoro). This module defines the shared interface (`TTSProvider`), a provider-
agnostic `Voice` dataclass, the per-provider leading-audio cleanup descriptor
(`OnsetProfile`), and factory helpers to construct the active provider and
look up its onset profile.

The `OnsetProfile` exists to fix a measured bug: the app's leading-silence
trim was hardcoded to a -13 dBFS threshold tuned to Edge's loud output. Kokoro
is ~2 dB quieter, so a fixed -13 dB threshold treats the ENTIRE Kokoro clip as
"silence" and trims it away. Each provider therefore declares how its opening
should be cleaned (fixed vs. relative threshold, trim cap, micro fade, boost).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from utils.logger import get_logger

logger = get_logger("TTS_PROVIDER")


@dataclass
class Voice:
    """A provider-agnostic TTS voice.

    A general version of EdgeVoice; the two are field-compatible so existing
    Edge call sites keep working.
    """
    voice_id: str
    name: str
    category: str
    description: str = ""


@dataclass
class OnsetProfile:
    """Per-provider description of how to clean the start of a TTS clip.

    Attributes:
        threshold_mode: "fixed" uses ``fixed_threshold_db`` directly;
            "relative" derives the threshold from the clip's own body level
            as ``body_dBFS - relative_offset_db``.
        fixed_threshold_db: Threshold (dBFS) used in "fixed" mode.
        relative_offset_db: dB below body level used in "relative" mode.
        trim_cap_ms: Maximum amount of leading audio that may be trimmed.
        micro_fade_ms: Length of the micro fade-in applied after a hard cut.
        boost_opening: Whether the opening should be volume-boosted.
        boost_window_ms: Window (ms) over which the opening boost applies.
        boost_slice_ms: Slice size (ms) for incremental boosting.
        max_boost_db: Maximum boost (dB) applied to any opening slice.
    """
    threshold_mode: str = "fixed"
    fixed_threshold_db: float = -13.0
    relative_offset_db: float = 8.0
    trim_cap_ms: int = 400
    micro_fade_ms: int = 3
    boost_opening: bool = True
    boost_window_ms: int = 60
    boost_slice_ms: int = 5
    max_boost_db: float = 12.0

    def resolve_threshold_db(self, audio) -> float:
        """Resolve the effective leading-silence threshold (dBFS) for ``audio``.

        In "fixed" mode this is simply ``fixed_threshold_db``. In "relative"
        mode it is the clip's body level minus ``relative_offset_db``, where
        the body is measured over [80ms : min(len, 3000)] to match the existing
        ``_level_opening`` window. Near-silent or empty clips fall back to a
        safe default of -40 dBFS so nothing is wrongly classified as silence.

        Args:
            audio: A ``pydub.AudioSegment``.

        Returns:
            The threshold in dBFS.
        """
        if self.threshold_mode != "relative":
            return self.fixed_threshold_db

        try:
            if len(audio) == 0:
                return -40.0
            body = audio[80:min(len(audio), 3000)]
            body_db = body.dBFS
            # dBFS is -inf for pure silence; guard NaN/-inf/near-silent.
            if body_db is None or body_db <= -50 or body_db != body_db:
                return -40.0
            return body_db - self.relative_offset_db
        except Exception:
            return -40.0


# Module-level onset profiles, one per provider.
# Edge clips start with digital silence, then a short volume ramp. The trim
# only has to remove the silence: a -13 dBFS threshold (used until 0.3.x) sat
# above the level of soft opening consonants and, with a 400 ms cap, took the
# first word of "Hi there" with it. -30 dBFS is below any spoken phoneme and
# above the noise floor; the opening boost below handles the ramp.
EDGE_ONSET = OnsetProfile(
    threshold_mode="fixed",
    fixed_threshold_db=-30.0,
    trim_cap_ms=250,
    micro_fade_ms=3,
    boost_opening=True,
    boost_window_ms=60,
    boost_slice_ms=5,
    max_boost_db=12.0,
)

# Kokoro's first ~40ms is a quiet onset RAMP (a real speech attack ~4-10 dB
# below body volume), not silence — so trimming can't/shouldn't remove it. To
# keep narration from starting soft, apply a GENTLE opening boost that lifts the
# attack toward the clip's body level. The boost is capped at 8 dB (gentler than
# Edge's 12 dB; measured to land within ~2 dB of body without hard-driving) and
# never exceeds body, so the start is clean without sounding clipped.
KOKORO_ONSET = OnsetProfile(
    threshold_mode="relative",
    relative_offset_db=8.0,
    trim_cap_ms=400,
    micro_fade_ms=3,
    boost_opening=True,
    boost_window_ms=60,
    boost_slice_ms=5,
    max_boost_db=8.0,
)

# Map provider id -> onset profile. Keep in sync with get_tts_provider.
_ONSET_BY_PROVIDER = {
    "edge_tts": EDGE_ONSET,
    "kokoro": KOKORO_ONSET,
}

_VALID_PROVIDERS = ("edge_tts", "kokoro")

# Human-readable display names by provider id (used by the UI provider picker
# and per-slide regeneration status text).
_PROVIDER_DISPLAY_NAMES = {
    "edge_tts": "Edge TTS",
    "kokoro": "Kokoro",
}


def provider_display_name(provider_id: Optional[str]) -> str:
    """Return a human-readable name for a provider id.

    Args:
        provider_id: A provider id ("edge_tts", "kokoro") or None.

    Returns:
        The display name; unknown/None ids fall back to "Edge TTS".
    """
    if not provider_id:
        return _PROVIDER_DISPLAY_NAMES["edge_tts"]
    return _PROVIDER_DISPLAY_NAMES.get(provider_id, _PROVIDER_DISPLAY_NAMES["edge_tts"])


def is_voice_for_provider(voice_id: Optional[str], provider_id: Optional[str]) -> bool:
    """Heuristically check whether ``voice_id`` belongs to ``provider_id``.

    Edge voice ids look like ``en-US-AriaNeural`` (contain a hyphen and end in
    "Neural"); Kokoro voice ids look like ``af_heart`` (short, underscore-
    separated, no hyphen). Used by per-slide regeneration to avoid sending an
    Edge voice id to Kokoro (or vice versa).

    Args:
        voice_id: The candidate voice id.
        provider_id: The active provider id.

    Returns:
        True if ``voice_id`` is plausibly valid for ``provider_id``.
    """
    if not voice_id:
        return False
    looks_edge = "-" in voice_id
    if provider_id == "kokoro":
        return not looks_edge
    # Default / edge_tts: treat hyphenated ids as Edge voices.
    return looks_edge


def effective_voice(
    override: Optional[str],
    default_voice: str,
    provider_id: Optional[str],
) -> str:
    """Pick the voice to use for a slide, honouring a per-slide override only
    when it belongs to the active provider.

    A slide may carry a ``voice_override`` that was set under a different
    provider (e.g. an Edge voice id while Kokoro is now active). Sending such an
    id to the active provider fails, so the override is used only when
    ``is_voice_for_provider`` confirms it matches ``provider_id``; otherwise the
    provider-correct ``default_voice`` is returned.

    Args:
        override: The slide's per-slide voice id, or None/"".
        default_voice: The provider-correct global voice to fall back to.
        provider_id: The active provider id ("edge_tts", "kokoro").

    Returns:
        The override when it is truthy and valid for ``provider_id``, else
        ``default_voice``.
    """
    if override and is_voice_for_provider(override, provider_id):
        return override
    return default_voice


def default_voice_for_provider(provider_id: Optional[str] = None) -> str:
    """Return the configured default voice id for a provider, config-only.

    This is the headless / refs-less counterpart to
    ``gui.helpers.resolve_active_voice_id``: it picks the provider-correct
    global default voice from ``config`` without touching any NiceGUI state or
    UI selects. Use it on headless code paths (scheduled jobs) so the
    voice id matches the active provider instead of a hardcoded Edge voice.

    Args:
        provider_id: One of "edge_tts" or "kokoro". When None, the value is
            read from ``config.tts_provider``.

    Returns:
        ``config.kokoro_voice`` when the provider is "kokoro", else
        ``config.edge_tts_voice``.
    """
    # Lazy import to avoid a circular import at module load time (same pattern
    # as get_tts_provider / get_onset_profile).
    from utils.config import config
    if provider_id is None:
        provider_id = config.tts_provider
    if provider_id == "kokoro":
        return config.kokoro_voice
    return config.edge_tts_voice


class TTSProvider(abc.ABC):
    """Abstract base class for text-to-speech providers.

    Concrete providers expose a stable ``provider_id`` and an
    ``onset_profile()`` describing how their clip openings are cleaned, plus
    the generation/voice/validation interface shared with EdgeTTSGenerator.
    """

    #: Stable identifier for this provider (e.g. "edge_tts", "kokoro").
    provider_id: str = ""

    @abc.abstractmethod
    def onset_profile(self) -> OnsetProfile:
        """Return the leading-audio cleanup profile for this provider."""

    @abc.abstractmethod
    def test_api_key(self) -> tuple:
        """Return (ok, message) describing provider availability."""

    @abc.abstractmethod
    def fetch_voices(self) -> List[Voice]:
        """Return the list of available voices."""

    @property
    @abc.abstractmethod
    def voices(self) -> List[Voice]:
        """Cached voice list from the most recent ``fetch_voices`` call."""

    @abc.abstractmethod
    def generate_audio(
        self,
        text: str,
        voice_id: str,
        output_path: Optional[Path] = None,
        speed: float = 1.0,
        use_cache: bool = True,
        model_id: str = "",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
    ) -> Optional[Path]:
        """Generate speech audio for ``text``; return the output path or None."""

    @abc.abstractmethod
    def get_remaining_characters(self) -> Optional[int]:
        """Return remaining character quota (large constant for local/free)."""


def get_tts_provider(provider_id: Optional[str] = None) -> TTSProvider:
    """Construct the active TTS provider.

    Args:
        provider_id: One of "edge_tts" or "kokoro". When None, the value is
            read from ``config.tts_provider``. Unknown ids fall back to Edge
            TTS with a logged warning.

    Returns:
        A concrete ``TTSProvider`` instance.
    """
    if provider_id is None:
        # Lazy import to avoid a circular import at module load time.
        from utils.config import config
        provider_id = config.tts_provider

    if provider_id == "kokoro":
        from core.kokoro_tts_generator import KokoroTTSGenerator
        return KokoroTTSGenerator()

    if provider_id != "edge_tts":
        logger.warning("Unknown TTS provider %r — falling back to edge_tts", provider_id)

    from core.edge_tts_generator import EdgeTTSGenerator
    return EdgeTTSGenerator()


def get_onset_profile(provider_id: Optional[str] = None) -> OnsetProfile:
    """Return the onset profile for a provider id.

    Args:
        provider_id: Provider id. When None, read from ``config.tts_provider``.
            Unknown ids return ``EDGE_ONSET``.

    Returns:
        The matching ``OnsetProfile`` (defaults to ``EDGE_ONSET``).
    """
    if provider_id is None:
        from utils.config import config
        provider_id = config.tts_provider
    return _ONSET_BY_PROVIDER.get(provider_id, EDGE_ONSET)


def suggest_voice_for_language(lang_subtag: str, voices: list) -> Optional[str]:
    """Pick the best available TTS voice for a target language.

    Used after translating speaker notes so narration is spoken in a voice that
    actually matches the language (translating to French but narrating with an
    English voice sounds broken).

    Args:
        lang_subtag: BCP-47 language subtag such as ``"fr"`` or ``"es"`` — the
            value from ``core.translator.LANGUAGES`` for the chosen language.
        voices: a list of ``Voice`` / ``EdgeVoice`` objects. Each has
            ``.category`` holding the full locale (e.g. ``"fr-FR"``) and
            ``.voice_id`` (e.g. ``"fr-FR-DeniseNeural"``).

    Returns:
        The ``voice_id`` of the best match, or ``None`` when nothing matches
        (callers keep the current voice on ``None``).
    """
    if not lang_subtag or not voices:
        return None
    want = lang_subtag.strip().lower()
    for v in voices:
        # Voices carry the locale in `.category` ("fr-FR"); fall back to the
        # locale embedded in the voice id ("fr-FR-DeniseNeural") if needed.
        locale = getattr(v, "category", "") or getattr(v, "voice_id", "") or ""
        lang = locale.split("-", 1)[0].strip().lower()
        if lang and lang == want:
            return getattr(v, "voice_id", None)
    return None
