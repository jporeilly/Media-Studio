"""Per-sentence narration overrides on a transcribed video's transcript.

Re-voicing pins every Whisper sentence to the moment it was spoken
(``services.processing._revoice_video``) and turns the leftover time back into
silence, which is why the new narration keeps step with the picture. It is also
why a narrator who spoke more slowly than the synthetic voice gets a re-voice
full of gaps: the pauses are faithfully reproduced. **Nothing here guesses a
better rate.** The answer is that the user nudges the one sentence that is
wrong, by hand, and this module is where those adjustments live.

An override is five optional keys **on the transcript segment itself**, in the
outer ``data/projects/<pid>/project.json``::

    {"start": 12.34, "end": 15.67, "text": "…",
     "offset": -0.40, "muted": false, "voice": "en-GB-RyanNeural",
     "provider": "edge_tts", "speed": 1.15}

On the segment rather than in a map keyed by index, because an index map has
the worse failure: re-transcribing changes the indices and the map silently
applies someone's offsets to *different* sentences. On the segment, a
re-transcribe wipes the overrides along with the sentences they belonged to -
visible loss instead of silent corruption. It also keeps a project "a directory
you can zip" (``services.projects``) and needs no new table.

**An older project has none of the keys and needs no migration**: absent means
the default, which is exactly today's behaviour.

This module is shaped like ``services.slides`` - the vocabulary, the validation,
and a read-modify-write under a per-pid lock - but the lock itself is NOT one of
its own: it is ``services.projects.project_lock``, which lives beside the file it
guards, because the transcript Save is a read-modify-write of the same file
(``set_transcript`` harvests these keys off the stored record) and a lock owned
by this module would have protected this writer only from itself. The record is
re-read inside the lock immediately before it is written, so an adjustment never
saves a stale copy over what a transcribe job wrote.

What deliberately is NOT here: anything that fits a speed automatically. See
``docs/porting/narration-timeline.md`` §9.
"""

import math

from services import projects as store
from services import studio_settings

# The override vocabulary: the keys a transcript segment may carry beyond
# ``start`` / ``end`` / ``text``. One tuple, read by the whole-list merge in
# ``services.projects.set_transcript`` and by the copy into the engine's
# ``original_segments`` in ``services.revoice``.
OVERRIDE_KEYS: tuple[str, ...] = ("offset", "muted", "voice", "provider", "speed")

# An offset only ever nudges a sentence within its own recording; five minutes
# either way is far beyond any real correction and keeps a typo from pinning a
# sentence into the next hour.
MAX_OFFSET_SECONDS = 300.0
MIN_SPEED = 0.5
MAX_SPEED = 2.0
MAX_VOICE_CHARS = 200

NARRATION_KINDS = ("video",)

_UNSET = object()


class ProjectNotFound(LookupError):
    """No project with that id (the routes answer 404). A dedicated class, so
    an IndexError or KeyError from a malformed record is not mistaken for it."""


# -- reading ----------------------------------------------------------------

def _record(pid: str) -> dict:
    """The outer record of a video project. ``ProjectNotFound`` when there is
    no such project, ``ValueError`` for a deck or a PDF."""
    record = store.get_project(pid)
    if not record:
        raise ProjectNotFound("Project not found.")
    if record.get("kind") not in NARRATION_KINDS:
        raise ValueError("Only video projects have a transcript.")
    return record


def _transcript(record: dict) -> list[dict]:
    transcript = record.get("transcript")
    return list(transcript) if isinstance(transcript, list) else []


def segments(pid: str) -> list[dict]:
    """Every transcript segment of a video project, overrides included, in
    order. [] when the video has not been transcribed yet."""
    return _transcript(_record(pid))


def spoken_count(pid: str) -> int:
    """How many segments would actually be synthesised: those with text that
    are not muted. Zero means a re-voice would produce no audio at all, which
    the re-voice route refuses rather than letting it fail as a bare
    "Re-voice failed" (``services.processing._revoice_video`` returns False on
    empty chunks)."""
    return count_spoken(segments(pid))


def count_spoken(transcript) -> int:
    """``spoken_count`` over a transcript already in hand - the re-voice route
    has the record, and re-reading it would be a second disk read for nothing."""
    return sum(
        1 for seg in (transcript or [])
        if isinstance(seg, dict) and (seg.get("text") or "").strip() and not seg.get("muted")
    )


# -- validation --------------------------------------------------------------

def _offset(value):
    """Seconds added to a sentence's pin, or None to clear the override.

    Positive pushes it later, negative earlier. Honest about what earlier
    means: the pin is a FLOOR, never a position - ``assemble_master`` inserts
    silence only when the gap is positive - so a sentence can always be pushed
    later but can only be pulled earlier as far as the previous sentence's
    synthesised audio actually ends.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"The offset must be a number of seconds between -{MAX_OFFSET_SECONDS:.0f} "
            f"and {MAX_OFFSET_SECONDS:.0f}, or empty for none."
        )
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"The offset must be a number of seconds between -{MAX_OFFSET_SECONDS:.0f} "
            f"and {MAX_OFFSET_SECONDS:.0f}, or empty for none."
        ) from None
    if not math.isfinite(seconds) or abs(seconds) > MAX_OFFSET_SECONDS:
        raise ValueError(
            f"The offset must be a number of seconds between -{MAX_OFFSET_SECONDS:.0f} "
            f"and {MAX_OFFSET_SECONDS:.0f}, or empty for none."
        )
    return round(seconds, 3)


def _muted(value):
    """True to leave a sentence out of the narration; None/False to speak it."""
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("Mute must be true or false.")
    return value or None


def _speed(value):
    """An explicit TTS speed for one sentence, or None for the job's own rule.

    A sentence that carries one bypasses ``_per_sentence_speed`` AND the
    post-synthesis tempo squeeze: the user asked for that length and the user's
    number wins. The overrun, if any, is absorbed at the next real pause.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"The speed must be a number between {MIN_SPEED} and {MAX_SPEED}, or empty for the default.")
    try:
        speed = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"The speed must be a number between {MIN_SPEED} and {MAX_SPEED}, or empty for the default."
        ) from None
    if not math.isfinite(speed) or not MIN_SPEED <= speed <= MAX_SPEED:
        raise ValueError(f"The speed must be a number between {MIN_SPEED} and {MAX_SPEED}, or empty for the default.")
    return round(speed, 3)


def _voice(value, provider) -> str | None:
    """A per-sentence voice id checked against ``provider`` (None = the studio's
    configured provider) the way the slide editor checks its own override, so an
    id from the other provider is refused now rather than silently dropped at
    re-voice time (``core.tts_provider.effective_voice``). None/"" clears it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("The voice override must be a voice id.")
    if not value.strip():
        return None
    if len(value) > MAX_VOICE_CHARS:
        raise ValueError(f"A voice id is limited to {MAX_VOICE_CHARS} characters.")
    return studio_settings.check_voice_for_provider(value, studio_settings.resolve_provider(provider))


def _segment(transcript: list[dict], index) -> dict:
    count = len(transcript)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
        if count == 0:
            raise ValueError("This video has no transcript yet - transcribe it first.")
        raise ValueError(
            f"No transcript segment with index {index!r}: this video has {count} "
            f"sentences (0 to {count - 1})."
        )
    return transcript[index]


# -- editing -----------------------------------------------------------------

def update_segment(
    pid: str, index: int, *,
    offset=_UNSET, muted=_UNSET, voice=_UNSET, speed=_UNSET, provider: str | None = None,
) -> dict:
    """Adjust one transcript sentence. A keyword left out is left alone;
    ``None`` clears that override back to the default.

    ``provider`` names the narration provider a ``voice`` belongs to
    (edge_tts | kokoro; None = the studio default) and is stored beside the
    voice so a later re-voice under the other provider falls back cleanly.
    Setting a voice to None clears the stored provider with it - a provider on
    its own would say nothing.

    Raises ``ProjectNotFound`` for an unknown project, ``ValueError`` for a
    deck/PDF, a bad index or a bad value; nothing is written then. The record
    is re-read inside ``services.projects.project_lock`` immediately before it
    is saved - the same lock the whole-list transcript Save takes - so neither
    a concurrent Save nor a transcribe's save is overwritten with a stale copy.
    """
    # Validate everything BEFORE the lock and before the first write: a refused
    # value must leave the project exactly as it was.
    checked: dict[str, object] = {}
    if offset is not _UNSET:
        checked["offset"] = _offset(offset)
    if muted is not _UNSET:
        checked["muted"] = _muted(muted)
    if speed is not _UNSET:
        checked["speed"] = _speed(speed)
    if voice is not _UNSET:
        resolved = _voice(voice, provider)
        checked["voice"] = resolved
        # The provider only ever travels with a voice.
        checked["provider"] = studio_settings.resolve_provider(provider) if resolved else None

    with store.project_lock(pid):
        record = _record(pid)
        transcript = _transcript(record)
        segment = dict(_segment(transcript, index))
        for key, value in checked.items():
            if value is None:
                segment.pop(key, None)
            else:
                segment[key] = value
        transcript[index] = segment
        record["transcript"] = transcript
        store.save_project(record)
        return segment
