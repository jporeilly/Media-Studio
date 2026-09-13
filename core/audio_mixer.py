"""Audio mixing functionality for combining narration with background music."""

import time
from pathlib import Path
from typing import List, Optional
from pydub import AudioSegment
from pydub.effects import normalize

from utils.logger import get_logger

logger = get_logger("AUDIO_MIXER")


def _load_audio_with_retry(path: Path, retries: int = 4, delay: float = 0.3) -> AudioSegment:
    """Load an audio file, retrying if the file is temporarily locked.

    Antivirus scanners (IObit, Defender) briefly hold an exclusive lock on
    newly-written files.  Retrying with a short back-off avoids the spurious
    'Permission denied' / CouldntDecodeError that FFmpeg raises in that window.
    """
    last_exc: Exception = RuntimeError("Unknown error")
    for attempt in range(retries):
        try:
            return AudioSegment.from_file(str(path))
        except Exception as exc:
            last_exc = exc
            logger.warning("_load_audio attempt %d/%d failed for %s: %s", attempt + 1, retries, path, exc)
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_exc


class AudioMixer:
    """Mixes narration audio with background music.

    Supports multiple background tracks that play sequentially.
    If the combined playlist is shorter than the narration, it loops.
    """

    def __init__(
        self,
        background_music_paths: Optional[List[Path]] = None,
        music_volume: float = 0.25,
        fade_duration: float = 2.0,
        # Legacy single-path support
        background_music_path: Optional[Path] = None,
    ):
        """
        Initialize audio mixer.

        Args:
            background_music_paths: Ordered list of background music files
            music_volume: Volume level for background music (0.0-1.0)
            fade_duration: Duration of fade in/out in seconds
            background_music_path: Legacy single path (converted to list)
        """
        # Accept either list or single path
        if background_music_paths:
            self.background_music_paths = list(background_music_paths)
        elif background_music_path:
            self.background_music_paths = [background_music_path]
        else:
            self.background_music_paths = []

        self.music_volume = music_volume
        self.fade_duration = fade_duration
        self._background_audio: Optional[AudioSegment] = None

        if self.background_music_paths:
            self._load_background_music()

    def _load_background_music(self):
        """Load and concatenate all background music files."""
        try:
            segments = []
            for p in self.background_music_paths:
                if p and p.exists():
                    seg = AudioSegment.from_file(str(p))
                    segments.append(seg)

            if segments:
                combined = segments[0]
                for seg in segments[1:]:
                    combined = combined + seg  # pydub concatenation
                self._background_audio = combined
            else:
                self._background_audio = None
        except Exception as e:
            print(f"Error loading background music: {e}")
            self._background_audio = None

    def _prepare_background(self, duration_ms: int) -> Optional[AudioSegment]:
        """Prepare background music for given duration, looping if necessary."""
        if self._background_audio is None:
            return None

        bg = self._background_audio

        # Loop if shorter than required duration
        if len(bg) < duration_ms:
            loops_needed = (duration_ms // len(bg)) + 1
            bg = bg * loops_needed

        # Trim to exact duration
        bg = bg[:duration_ms]

        # Convert linear volume (0.0-1.0) to decibels.
        # At volume=1.0 -> 0 dB (full), volume=0.25 -> -15 dB, volume=0.0 -> -20 dB.
        volume_db = 20 * (self.music_volume - 1)
        bg = bg + volume_db

        # Apply fade in/out
        fade_ms = int(self.fade_duration * 1000)
        if fade_ms > 0 and len(bg) > fade_ms * 2:
            bg = bg.fade_in(fade_ms).fade_out(fade_ms)

        return bg

    def mix_single(
        self,
        narration_path: Path,
        output_path: Path,
        add_background: bool = True
    ) -> bool:
        """
        Mix a single narration file with background music.

        Args:
            narration_path: Path to narration audio file
            output_path: Path to save mixed audio
            add_background: Whether to add background music

        Returns:
            True if successful, False otherwise
        """
        try:
            narration = _load_audio_with_retry(narration_path)

            if add_background and self._background_audio is not None:
                bg = self._prepare_background(len(narration))
                if bg is not None:
                    # Overlay narration on top of background
                    mixed = bg.overlay(narration)
                else:
                    mixed = narration
            else:
                mixed = narration

            # Normalize audio levels
            mixed = normalize(mixed)

            # Export to temp file then atomic rename to avoid lock conflicts
            output_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = output_path.with_suffix(".tmp.mp3")
            try:
                mixed.export(str(tmp), format="mp3")
                tmp.replace(output_path)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            return True

        except Exception as e:
            print(f"Error mixing audio: {e}")
            return False
