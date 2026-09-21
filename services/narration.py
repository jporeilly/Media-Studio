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

It also speaks ONE stored sentence on demand (``preview_segment``), so a
per-sentence voice or speed can be heard before a whole re-voice is run to find
out what it sounds like. That is a read: it writes nothing to the project, it is
deliberately not a job, and the audio it leaves in the shared TTS cache is the
very entry the render will reuse.

And it writes the transcript out as a file (``export_transcript``: SRT, TXT or
JSON) in two timing views - as the re-voice will speak it, projected through the
edit and placed by the very calls the audition plan is placed by, or as it was
spoken in the source. Another read: nothing on the project changes.

What deliberately is NOT here: anything that fits a speed automatically. See
``docs/porting/narration-timeline.md`` §9.
"""

import json
import math
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from core.subtitle_generator import format_srt_time
from services import projects as store
from services import studio_settings
from services.voices import KOKORO_MODEL_PENDING
from utils.helpers import get_cache_path, sanitize_filename
from utils.logger import get_logger

if TYPE_CHECKING:
    # ``services.edit`` imports this module, so at runtime it is imported
    # lazily inside the functions that need it; this is for the annotations only.
    from services import edit

logger = get_logger("NARRATION")

# The override vocabulary: the keys a transcript segment may carry beyond
# ``start`` / ``end`` / ``text``. One tuple, read by the whole-list merge in
# ``services.projects.set_transcript`` and by the copy into the engine's
# ``original_segments`` in ``services.revoice``.
OVERRIDE_KEYS: tuple[str, ...] = ("offset", "muted", "voice", "provider", "speed")

# An offset only ever nudges a sentence within its own recording; five minutes
# either way is far beyond any real correction and keeps a typo from pinning a
# sentence into the next hour.
MAX_OFFSET_SECONDS = 300.0
# How many sentences one batch offsets write may carry (``update_offsets``).
# Every index must be unique and inside the transcript, so a real batch is
# bounded by the transcript itself; this is a bound on abuse, not on use.
MAX_OFFSET_BATCH = 5000
MIN_SPEED = 0.5
MAX_SPEED = 2.0
MAX_VOICE_CHARS = 200

# The speed a sentence is previewed at when neither the caller nor the sentence
# names one - the same default ``RevoiceRequest.speed`` carries, so a bare
# preview plays at the rate a bare re-voice would use.
DEFAULT_SPEED = 1.0

# As much of one sentence as a preview will speak. A Whisper sentence is nowhere
# near this; the cap is there so a pathological edit cannot turn one press of a
# Play button into a minutes-long synthesis.
MAX_PREVIEW_CHARS = 1000

# How long the request waits for the provider. Edge TTS is a network round trip
# (typically 1-3 s for a sentence) whose own ceiling is 120 s
# (``core.edge_tts_generator._run_async``), and its ``generate_audio`` swallows
# every exception and returns None - so without a bound of our own a hung
# request holds a server thread for two minutes and then answers 500. Bounded
# here, the answer is a readable 502 and the synthesis thread (a daemon) is left
# to finish into the cache on its own.
PREVIEW_TIMEOUT_SECONDS = 30.0

# How long the audition plan waits for the speaking-rate measurement. That is
# THREE syntheses rather than the preview's one, and the first of them pays the
# same cold start (~10 s on this machine), so it is sized above
# PREVIEW_TIMEOUT_SECONDS rather than equal to it. Past this the plan answers
# with the engine's default rate instead of holding the request; the measuring
# thread is left to finish into the shared cache, so the next press is quick.
BASELINE_TIMEOUT_SECONDS = 45.0

# Measured speaking rates, keyed on (project, provider, voice). The plan is
# re-fetched on every tab open past react-query's staleTime and on every change
# to the Re-voice card's narration; without this, each one re-paid three
# syntheses. Dropped by ``forget_baseline`` whenever the transcript changes or
# the project is deleted, so nothing here outlives what it was measured from.
_BASELINE_CACHE: dict[tuple[str, str, str], float] = {}
_BASELINE_GUARD = threading.Lock()

NARRATION_KINDS = ("video",)

_UNSET = object()


class ProjectNotFound(LookupError):
    """No project with that id (the routes answer 404). A dedicated class, so
    an IndexError or KeyError from a malformed record is not mistaken for it."""


class SegmentNotFound(LookupError):
    """No transcript sentence at that index - the video has not been
    transcribed, or the index is past the end.

    Its own class because the two routes answer it differently and both are
    right: the PATCH is a write whose argument is out of range (400, as it has
    answered since phase 1), while the preview is a GET of a thing that is not
    there (404, as the spec names it). The message is the same either way, so it
    is written once, here."""


class KokoroModelMissing(RuntimeError):
    """Kokoro can speak, but its ~340 MB model is not on disk yet (409).

    Probed with ``kokoro_model_present()`` - a stat-only check that never
    downloads - so pressing Play cannot silently start a 340 MB download and
    hold the request until it finishes."""


class PreviewUnavailable(RuntimeError):
    """The provider returned no audio for this sentence (502). Either it
    answered nothing (``generate_audio`` returns None on any failure) or it did
    not answer inside ``PREVIEW_TIMEOUT_SECONDS``."""


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
            raise SegmentNotFound("This video has no transcript yet - transcribe it first.")
        raise SegmentNotFound(
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

    Raises ``ProjectNotFound`` for an unknown project, ``SegmentNotFound`` for
    an index past the end, ``ValueError`` for a deck/PDF or a bad value;
    nothing is written in any of those cases. The record
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
    # Muting a sentence changes which three the speaking rate is measured from
    # (the measurement skips muted ones), so the memoised rate no longer
    # describes this transcript. Outside the lock: it touches a different one.
    forget_baseline(pid)
    return segment


def update_offsets(pid: str, offsets) -> list[dict]:
    """Set several sentences' offsets in ONE read-modify-write.

    ``offsets`` is ``[(index, seconds_or_None), ...]``: a drag of twelve
    blocks along the timeline, a nudge of the selected ones, or a Reset
    timing (``None`` clears the key). Every value goes through the rule
    ``update_segment`` applies (``_offset``: within ±MAX_OFFSET_SECONDS,
    finite, three decimals) and every index must be a whole number, unique
    and inside the transcript - all checked BEFORE the write, so a refused
    entry leaves the project exactly as it was. A value that rounds to zero
    is stored as ABSENT, like ``None``: that is the List view's own rule for
    its Offset box (``lib/narration.ts::numberChange``), applied here so a
    block dragged back to where it was spoken leaves no key behind. Then one
    write, under ``services.projects.project_lock``: twelve single-sentence
    writes would each re-read and rewrite the whole record, interleaving
    with each other and with the transcript Save (the edit timeline spec's
    trap 21).

    The memoised speaking rate is NOT dropped: an offset moves where a
    sentence is pinned, not the texts the rate is measured over.

    Returns the updated sentences, ``[{"index": i, ...segment}, ...]`` in
    index order. Raises ``ProjectNotFound``, ``SegmentNotFound`` (naming the
    index that is outside the transcript) or ``ValueError`` (a deck, no
    entries, an index that is not a whole number or is given twice, a bad
    value); nothing is written in any of those cases.
    """
    checked: dict[int, float | None] = {}
    for index, value in offsets:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError(f"Segment index {index!r} must be a whole number.")
        if index in checked:
            raise ValueError(f"Segment {index} is given twice; name each sentence's offset once.")
        seconds = _offset(value)
        checked[index] = None if not seconds else seconds  # 0.0 (and -0.0) clear the key
    if not checked:
        raise ValueError("Give at least one sentence's offset.")

    with store.project_lock(pid):
        record = _record(pid)
        transcript = _transcript(record)
        # Every index resolved before any is applied: an index past the end
        # in the twelfth entry must not leave the first eleven written.
        updated = {index: dict(_segment(transcript, index)) for index in checked}
        for index, value in checked.items():
            if value is None:
                updated[index].pop("offset", None)
            else:
                updated[index]["offset"] = value
            transcript[index] = updated[index]
        record["transcript"] = transcript
        store.save_project(record)
    return [{**updated[index], "index": index} for index in sorted(updated)]


# -- auditioning the whole narration -----------------------------------------

def _job_narration(provider, voice, speed) -> tuple[str, str, float]:
    """The (provider, voice, speed) a re-voice would run with: the Re-voice
    card's three values, each falling back to the studio's.

    The same resolution ``_preview_narration`` does for the job half of a
    preview, before any per-sentence override is applied on top. Raises
    ``ValueError`` (400) for an unknown provider, a voice belonging to the other
    provider, or a speed out of range.
    """
    provider_id, studio_voice = studio_settings.resolve_narration(provider, None)
    job_voice = (
        studio_settings.check_voice_for_provider(voice, provider_id)
        if (voice or "").strip() else studio_voice
    )
    asked = _speed(speed)
    return provider_id, job_voice, DEFAULT_SPEED if asked is None else asked


def forget_baseline(pid: str) -> None:
    """Drop this project's memoised speaking rates.

    Called when the project is deleted and whenever its transcript changes -
    editing the words, or muting a sentence - because the measurement samples
    three of those sentences and skips the muted ones. Re-measuring afterwards
    is nearly free anyway: the samples are keyed on (text, voice, speed) in the
    shared TTS cache, so unless the sampled sentences THEMSELVES were edited,
    the three synthesis calls are three file reads.
    """
    with _BASELINE_GUARD:
        for key in [k for k in _BASELINE_CACHE if k[0] == pid]:
            del _BASELINE_CACHE[key]


def baseline_rate(pid: str, transcript, provider_id: str, voice_id: str) -> float:
    """The TTS speaking rate the render will fit sentences against.

    The real measurement (``services.processing.calibrate_tts_baseline``):
    three sample sentences synthesised at speed 1.0 and measured. They go
    through the shared cache keyed on (text, voice, speed), so the audition
    pays for them once and the re-voice that follows reuses the very entries -
    which is the same reason a preview is free the second time.

    **Bounded** at ``BASELINE_TIMEOUT_SECONDS``, with the same daemon-thread and
    join idiom ``_synthesise`` uses and for the same reason: ``generate_audio``
    has no timeout argument, Edge's internal ceiling is 120 s, and this makes
    THREE calls - so an unreachable provider would otherwise hold a server
    thread for six minutes and then answer 200 with the default rate in it,
    behind a page that says "Reading the narration…" and offers no way out. The
    thread is left to finish into the cache on its own; only the waiting stops.

    **Memoised** per (project, provider, voice), because the plan is fetched
    again on every tab open past react-query's staleTime and on every change to
    the Re-voice card - three identical plan GETs used to mean three
    calibrations. The transcript is part of the key only in the sense that any
    change to it drops the entry (``forget_baseline``).

    Every failure falls back to the engine's own default rather than failing the
    audition: an unreachable provider, a missing pydub, a timeout, and -
    deliberately - Kokoro with no model on disk, because the first Kokoro
    synthesis downloads ~340 MB and asking for a timeline must not start that.
    The plan then reports the speeds a render with an unmeasurable baseline
    would use, which is exactly what such a render would do. A fallback is NOT
    memoised: the provider may be reachable again by the next press.
    """
    from services import processing

    key = (pid, provider_id, voice_id)
    with _BASELINE_GUARD:
        held = _BASELINE_CACHE.get(key)
    if held is not None:
        return held

    try:
        if provider_id == "kokoro":
            from core.kokoro_tts_generator import kokoro_model_present

            if not kokoro_model_present():
                return processing.DEFAULT_BASELINE_RATE

        from core.tts_provider import get_tts_provider

        generator = get_tts_provider(provider_id)
        tmp_dir = Path(tempfile.mkdtemp(prefix="narration_plan_"))
        outcome: dict = {}

        def _measure() -> None:
            try:
                outcome["rate"] = processing.calibrate_tts_baseline(
                    transcript, generator, tmp_dir,
                    provider=provider_id, voice_id=voice_id,
                )
            except Exception as exc:  # pragma: no cover - the measurement swallows its own
                outcome["error"] = exc
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        worker = threading.Thread(target=_measure, name="tts-baseline", daemon=True)
        worker.start()
        worker.join(BASELINE_TIMEOUT_SECONDS)
        if worker.is_alive():
            logger.warning(
                "The speaking rate could not be measured in %.0fs; the timeline will use %.1f chars/s",
                BASELINE_TIMEOUT_SECONDS, processing.DEFAULT_BASELINE_RATE,
            )
            return processing.DEFAULT_BASELINE_RATE
        rate = outcome.get("rate")
        if rate is None:
            return processing.DEFAULT_BASELINE_RATE
    except Exception:
        # Never a 500 for want of a speaking rate: the timeline is still worth
        # drawing, and the sentences that carry an explicit speed do not use the
        # baseline at all.
        logger.warning("Could not calibrate the speaking rate for the audition plan", exc_info=True)
        return processing.DEFAULT_BASELINE_RATE

    with _BASELINE_GUARD:
        _BASELINE_CACHE[key] = rate
    return rate


def transcript_section(transcript) -> dict:
    """The ONE section a re-voice reconstructs from a transcript: it spans the
    whole of it, from the first sentence's start to the last one's end.

    Stated once because two places need it and they must agree.
    ``services.revoice`` writes it onto the engine's single slide
    (``original_start_time`` / ``original_end_time``), and :func:`plan` needs it
    to work out the window the LAST sentence has - which is bounded by this
    section's end whenever the video's own duration is not known
    (``_probe_duration`` returns nothing without ffprobe, which the packaged app
    may not have). Two copies of "the section is the whole transcript" would be
    free to drift, and the timeline would then advertise a rate for the final
    sentence that the render does not use.
    """
    from services import processing

    if not transcript:
        return {"start": 0.0, "end": 0.0}
    # A hand-edited project.json can hold anything at all in the list. Both
    # callers used to crash on a non-dict first or last entry - here with an
    # AttributeError inside a 500, and in ``services.revoice`` with a bare
    # "Re-voice failed" - so the coercion belongs in the one place they share.
    first = transcript[0] if isinstance(transcript[0], dict) else {}
    last = transcript[-1] if isinstance(transcript[-1], dict) else {}
    return {
        "start": processing._seconds(first.get("start"), 0.0),
        "end": processing._seconds(last.get("end"), 0.0),
    }


def _source_length(record: dict, source_duration, section: dict) -> float:
    """How long the source is when the WAV cannot say (``source_duration``
    None): the record's own duration (measured on the video at transcription),
    else the transcript's last word. One fallback chain for the audition plan
    and the transcript download, so a project restored without its
    ``audio.wav`` is the same length to both."""
    if source_duration is not None:
        return source_duration
    return float(record.get("duration") or 0.0) or section["end"]


@dataclass(frozen=True)
class Projection:
    """The narration as the render will speak it: the transcript projected
    through the edit, every spoken sentence at its pin, and the ones the
    render will drop named. The half of :func:`plan` that
    :func:`export_transcript` shares with it, pulled out so the transcript a
    user downloads is placed by the very calls the audition is placed by,
    never by a copy of them.

    ``listed`` pairs every projected sentence with its index in the STORED
    transcript; ``spoken`` is the subset the render will synthesise (words,
    not muted), in order; ``pins`` is where each spoken sentence is pinned;
    ``windows`` the room each one has - absent for a ``past_end`` one, which
    the mux drops; ``duration`` is the length everything is bounded by, the
    picture's when it is cut, else ``source_length``, the source's."""

    applied: "edit.Applied"
    source_duration: float | None
    source_length: float
    duration: float
    section: dict
    listed: list[tuple[int, object]]
    spoken: list[tuple[int, dict]]
    pins: dict[int, float]
    windows: dict[int, float]
    past_end: frozenset[int]


def project_narration(record: dict, transcript: list) -> Projection:
    """The transcript through the edit, every spoken sentence at its pin, and
    the ones the render will drop - by the render's own calls
    (``services.edit.apply``, :func:`transcript_section`,
    ``services.processing.sentence_window`` and, inside it, ``_pin``), so the
    audition plan and the transcript download cannot disagree with the render,
    or with each other, about which sentences are spoken and where each is
    aimed. Raises ``ValueError`` for an edit that cannot be read or measured.
    """
    from services import edit, processing, waveform

    pid = record["id"]

    # The scale everything is drawn against: the WAV header's, never ffprobe's
    # and never the record's if the audio can speak for itself.
    #
    # **Where this diverges from the render, exactly.** ``_revoice_video`` bounds
    # its LAST spoken sentence by ``_probe_duration(video)`` and drops any
    # sentence pinned at or past that. Two differences follow, and both are
    # accepted rather than hidden:
    #
    # - when ffprobe IS available the two numbers are the same recording
    #   measured two ways (the extracted audio is that video's own audio), so
    #   they agree to within a frame;
    # - when ffprobe is ABSENT - which the packaged app must assume, since the
    #   imageio fallback ships ffmpeg only - the render gets ``video_end = 0.0``
    #   and falls back to bounding the last sentence by the SECTION's end, while
    #   this still uses the WAV duration. Those genuinely differ whenever the
    #   recording runs on after the last word.
    #
    # Blast radius: the last spoken sentence only, and only when the bound would
    # make it short enough to be sped up - a longer window can only lower a
    # speed to the floor it is already at. Using ffprobe here instead is refused
    # by the porting spec's traps 5 and 6; using the record's duration would not
    # be the scale of the file being drawn.
    source_duration = waveform.duration_for(pid)

    # The edit, applied by the very function the render applies it with, so
    # the two cannot disagree about whether it changes anything or where a
    # sentence lands once it has. With no edit, or one whose narration list
    # keeps everything (a video-only edit included), ``sentences`` is the
    # transcript itself and nothing below changes. ``listed`` pairs each
    # sentence with its index in the STORED transcript - what the narration
    # routes address a sentence by; the projection drops sentences, so a
    # position in its list is not it. Keyed on ``projected``, never on
    # ``cut``: a video-only edit projects nothing and a narration-only edit
    # projects everything.
    applied = edit.apply(record, transcript, source_duration)
    listed = (
        [(seg["index"], seg) for seg in applied.sentences] if applied.projected
        else list(enumerate(transcript))
    )

    # ONE section spanning the whole transcript - the very thing
    # ``services.revoice`` reconstructs for the engine, so the window the last
    # sentence is measured against is the one the render will measure it
    # against. Over the projected sentences when there is a cut, as the
    # render's is.
    section = transcript_section(applied.sentences)
    source_length = _source_length(record, source_duration, section)
    # The PICTURE's length when it is cut: the mux is ``-shortest``, so that
    # is what bounds the last sentence and drops one pinned past it.
    duration = applied.output_duration if applied.cut else source_length

    # The sentences the render will actually speak, in order - empty and muted
    # ones filtered out BEFORE any window maths, exactly as ``_revoice_video``
    # filters them, which is what gives the sentence before a muted one its room.
    spoken_pairs = [
        (index, seg) for index, seg in listed
        if isinstance(seg, dict) and (seg.get("text") or "").strip() and not seg.get("muted")
    ]
    spoken = [(seg, section) for _, seg in spoken_pairs]

    # Where each spoken sentence is pinned and the window it has, and -
    # separately - the ones the render will throw away because their pin is at
    # or past the end of the video. ``replace_video_audio`` muxes with
    # ``-shortest``, so such a sentence is not in the render at all and is
    # counted a failure; the timeline must mark it and must not audition it,
    # and the download must not list it, or they would be promising audio the
    # render will never produce. The pin is recorded for a dropped sentence
    # too, so a caller can say where it was aimed.
    pins: dict[int, float] = {}
    windows: dict[int, float] = {}
    past_end: set[int] = set()
    for position, (index, _) in enumerate(spoken_pairs):
        pin, next_start = processing.sentence_window(spoken, position, duration)
        pins[index] = pin
        if duration > 0 and pin >= duration:
            past_end.add(index)
            continue
        windows[index] = max(0.0, next_start - pin)

    return Projection(
        applied, source_duration, source_length, duration, section,
        listed, spoken_pairs, pins, windows, frozenset(past_end),
    )


def plan(pid: str, *, provider=None, voice=None, speed=None) -> dict:
    """What every sentence says, how fast, and in whose voice - for auditioning
    the narration in the browser without rendering anything.

    **The server owns what each sentence says and how fast; the client owns only
    when each clip lands.** The re-voice's rate rules are not reimplementable in
    TypeScript without becoming a second, drifting copy of them, so they are
    answered here from the very functions the render calls
    (``services.processing.sentence_window`` /
    ``sentence_speed`` / ``calibrate_tts_baseline``). What is left for the client
    is the one piece of arithmetic only it can do: where each clip actually
    lands, which needs the real decoded length of each clip.

    Each sentence comes back with

    - ``start`` / ``end`` - the moment it was SPOKEN, which is what lines up
      with the waveform underneath;
    - ``pinned_start`` - where the render will pin it (``start`` + its offset,
      floored at zero). A pin is a FLOOR, not a position: a clip that overruns
      pushes the next one late, which is why the client schedules against real
      clip lengths rather than trusting these;
    - ``speed`` and ``voice`` - the EFFECTIVE values, i.e. what the render will
      actually synthesise with, per-sentence overrides already applied;
    - ``preview_url`` - carrying those effective values, so the clip the browser
      fetches is byte-identical to the one the render will reuse from the cache.
      (Before this, a preview was only exact for a sentence carrying an explicit
      speed; every other sentence could still be sped up a little at render time
      to fit its window, and the audition would not have heard that.)

    ``provider`` / ``voice`` / ``speed`` are the JOB's - what the Re-voice card
    has selected - exactly as the preview route takes them.

    Muted and empty sentences are returned too, so the timeline can draw them,
    but they take no part in the window maths: the render filters them out
    before it measures anything, which is what gives the sentence BEFORE a muted
    one its room. Their ``speed`` is the job's (or their own explicit one) and
    nothing is synthesised for them.

    **The edit is applied here, server-side** (``services.edit``): when the
    project's NARRATION list removes anything, every sentence comes back in
    TIMELINE seconds - where it lands in the output, holes closed - and a
    sentence whose start was cut is not in the list at all, exactly as it is
    not in the render; when its VIDEO list removes anything, ``duration`` is
    the picture's output length. The two lists close their holes from the
    same zero, so a sentence pinned at or past the cut picture's end is
    ``past_end`` exactly as one pinned past an uncut video is. The projection
    is the very function the render calls (``services.edit.apply``), never a
    copy of it, for the same reason the rate rules are: the client must never
    reimplement render arithmetic. ``edit`` in the payload is the edit the
    sentences were projected through, one block per track, so the timeline
    draws the one the plan was made from.

    Writes nothing, so - like the preview - it deliberately does not take
    ``jobs.require_idle``: refusing to let someone audition while a re-voice
    runs would be a 409 on a read.

    Raises ``ProjectNotFound`` (404), ``SegmentNotFound`` (404, no transcript)
    or ``ValueError`` (400: a deck/PDF, an unknown provider, a voice from the
    other provider, a speed out of range, an edit that cannot be read or
    measured).
    """
    from core.tts_provider import effective_voice
    from services import edit, processing

    record = _record(pid)
    transcript = _transcript(record)
    if not transcript:
        raise SegmentNotFound("This video has no transcript yet - transcribe it first.")

    provider_id, job_voice, job_speed = _job_narration(provider, voice, speed)

    # The transcript through the edit, every spoken sentence at its pin and
    # the room it has, by the render's own calls. A helper of its own because
    # the transcript download (``export_transcript``) places its sentences by
    # exactly the same calls; see ``project_narration`` for where each number
    # comes from, and for where the plan knowingly diverges from the render.
    projected = project_narration(record, transcript)
    applied, section, duration = projected.applied, projected.section, projected.duration
    windows, past_end = projected.windows, projected.past_end

    # Measured over the sentences the render will measure it over: the
    # projected ones when there is a cut (``_revoice_video`` calibrates on the
    # segments it is handed, and it is handed the projection).
    rate = (
        baseline_rate(pid, applied.sentences, provider_id, job_voice)
        if projected.spoken else processing.DEFAULT_BASELINE_RATE
    )

    sentences = []
    for index, seg in projected.listed:
        if not isinstance(seg, dict):
            # A hand-edited project.json can hold anything. The window pass above
            # skips a non-dict segment; this one must not then answer 500 on it.
            seg = {}
        text = (seg.get("text") or "").strip()
        seg_voice = effective_voice(seg.get("voice"), job_voice, provider_id)
        # A sentence with no window (muted, wordless, or pinned outside the
        # video) is never synthesised; its own explicit speed still stands, so
        # unmuting it in the list view shows the rate it would be spoken at.
        window = windows.get(index)
        seg_speed = processing.sentence_speed(
            seg, text, window if window is not None else 0.0, rate, job_speed,
        )
        sentences.append({
            "index": index,
            "text": seg.get("text") or "",
            "start": processing._seconds(seg.get("start"), 0.0),
            "end": processing._seconds(seg.get("end"), 0.0),
            "pinned_start": processing._pin(seg, section),
            "muted": bool(seg.get("muted")),
            # Whether the render will synthesise this sentence at all. The
            # client must NOT re-derive it: "has words" is ``str.strip()`` here
            # and would be ``String.trim()`` there, and those are different
            # character sets (U+001C-1F strip but do not trim; U+FEFF trims but
            # does not strip). A pasted control character would then be
            # auditioned by the browser and answered 400 by the preview route,
            # surfacing as a per-sentence failure with no cause anyone could see.
            "speakable": index in windows,
            # ... and when it is not speakable DESPITE having words and not being
            # muted, this says why: its offset pins it at or past the end of the
            # video, so the mux drops it.
            "past_end": index in past_end,
            # The room the render measured for it, and whether the render may
            # tempo-squeeze it into that room after synthesis. Both are the
            # client's to model, because only the client knows how long the clip
            # really turned out to be - see ``squeeze_tolerance`` below.
            "window": window if window is not None else 0.0,
            "squeezable": index in windows and processing._explicit_speed(seg) is None,
            "speed": seg_speed,
            "voice": seg_voice,
            "preview_url": preview_url(pid, index, provider_id, seg_voice, seg_speed),
        })

    return {
        "duration": duration,
        "baseline_rate": rate,
        # The post-synthesis tempo squeeze's two numbers, sent rather than
        # written into the client as literals: they are the RENDER's constants
        # (``_revoice_video``), and a second copy in TypeScript would be free to
        # drift from the loop it is supposed to be predicting.
        "squeeze_tolerance": processing.SQUEEZE_TOLERANCE,
        "squeeze_max_factor": processing.SQUEEZE_MAX_FACTOR,
        # The edit the sentences above were projected through - each track's
        # kept ranges in SOURCE seconds (null = everything), the music clips
        # (each with its file's length and whether the file has gone), the
        # source's length and the output's - so the client draws the very
        # edit the plan was made from rather than fetching it separately and
        # risking a newer one.
        "edit": edit.payload(applied.video, applied.narration, projected.source_duration, applied.music),
        "sentences": sentences,
    }


def preview_url(pid: str, index: int, provider_id: str, voice_id: str, speed: float) -> str:
    """Where the browser fetches one sentence's audio from, carrying the
    EFFECTIVE voice and speed.

    Carrying them is what makes the audition honest rather than approximate: the
    preview route applies the sentence's own overrides on top of whatever it is
    given, so passing the effective pair either matches what it would have
    resolved anyway or supplies the per-sentence rate the render computed - and
    the clip that comes back is then the byte-identical cache entry the re-voice
    will reuse.
    """
    query = urlencode({"provider": provider_id, "voice": voice_id, "speed": f"{speed:g}"})
    return f"/api/projects/{pid}/transcript/{index}/preview?{query}"


# -- downloading the transcript ----------------------------------------------

# The files a transcript can be downloaded as: format -> (media type, extension).
EXPORT_FORMATS: dict[str, tuple[str, str]] = {
    "srt": ("application/x-subrip", "srt"),
    "txt": ("text/plain; charset=utf-8", "txt"),
    "json": ("application/json", "json"),
}

# What follows the project's name in the file's name: ``<name>-narration.<ext>``.
# The route reads it too, to tell an ASCII fallback whose stem folded away to
# nothing from one that did not.
EXPORT_SUFFIX = "-narration"

# The two timing views, each with what its timings ARE - said inside the file
# (the txt header, the json ``note``), because a number on its own reads as a
# promise. The timeline view's timings are where each sentence is AIMED: a pin
# is a floor, never a position (see ``_offset``), so a sentence whose clip runs
# long lands later than the file says. The SRT cannot carry the note - players
# ignore comments, and a cue that said it would be a subtitle.
EXPORT_VIEWS: dict[str, str] = {
    "timeline": (
        "timings are where each sentence is aimed; a sentence whose clip runs long "
        "is placed later by the render"
    ),
    "source": (
        "timings are where each sentence was spoken in the source video; a muted "
        "sentence is one the re-voice leaves out"
    ),
}

# The shortest cue the SRT writes. A zero-length or reversed span (a
# hand-edited record, or a sentence Whisper timed to one instant) is one a
# player never shows; half a second is the least it takes to be seen.
MIN_CUE_SECONDS = 0.5


# ``HH:MM:SS,mmm``, SRT's own clock - the engine's, so the transcript download
# and the generate job's .srt sidecar can never disagree on a timestamp. Rounded
# to whole milliseconds FIRST and split from there (1.9996 s is ``00:00:02,000``,
# never ``00:00:01,1000``); negative is clamped at zero, as a pin is.
srt_time = format_srt_time


def timecode(seconds: float) -> str:
    """``m:ss.mmm`` - the List view's own label (``lib/format.ts::timecode``),
    so a line of the text file reads as the row it came from. Whole
    milliseconds first, for the same reason as :func:`srt_time`."""
    ms = int(round(max(0.0, seconds) * 1000))
    minutes, ms = divmod(ms, 60_000)
    return f"{minutes}:{ms / 1000:06.3f}"


def _one_line(text) -> str:
    """A sentence on one line: newlines, and any run of whitespace, collapsed
    to a single space. The transcript editor is a textarea, so a saved
    sentence can carry a newline, and the SRT and the text file are both one
    line per sentence."""
    return " ".join((text or "").split())


def _cue(index: int, start: float, end: float, seg: dict) -> dict:
    """One sentence as the writers see it: its index in the STORED transcript,
    where it starts and ends in the view's seconds, and its words, mute and
    offset as saved."""
    return {
        "index": index, "start": start, "end": end,
        "text": seg.get("text") or "", "muted": bool(seg.get("muted")), "offset": seg.get("offset"),
    }


def _timeline_cues(record: dict, transcript: list) -> tuple[float, list[dict]]:
    """The ``timeline`` view: the narration as the re-voice will speak it.

    The sentences ``edit.apply`` returns, projected through the NARRATION
    list; muted and wordless ones left out, exactly as ``_revoice_video``
    leaves them out; a sentence pinned at or past the picture's end left out,
    as the mux drops it; each remaining one at its PIN, running for its spoken
    length (``end - start``) and clamped to the picture's output length. All
    of it read off :func:`project_narration` - the audition plan's own
    numbers, never a second projection or a second pin.
    """
    from services import processing

    projected = project_narration(record, transcript)
    cues = []
    for index, seg in projected.spoken:
        if index in projected.past_end:
            continue
        pin = projected.pins[index]
        spoken_for = max(
            0.0,
            processing._seconds(seg.get("end"), 0.0) - processing._seconds(seg.get("start"), 0.0),
        )
        end = pin + spoken_for
        if projected.duration > 0:
            end = min(end, projected.duration)
        cues.append(_cue(index, pin, end, seg))
    return projected.duration, cues


def _source_cues(record: dict, transcript: list) -> tuple[float, list[dict]]:
    """The ``source`` view: the transcript as stored - every sentence, at
    Whisper's own ``start`` / ``end``, muted ones included and marked. No edit
    is applied and none is needed, so a project whose edit can no longer be
    measured (its audio removed by hand) still has this view."""
    from services import processing, waveform

    section = transcript_section(transcript)
    length = _source_length(record, waveform.duration_for(record["id"]), section)
    cues = [
        _cue(index, processing._seconds(seg.get("start"), 0.0), processing._seconds(seg.get("end"), 0.0), seg)
        for index, seg in enumerate(transcript) if isinstance(seg, dict)
    ]
    return length, cues


def _marked(cue: dict) -> str:
    """The cue's words on one line, ``[muted]`` in front when the sentence is
    (only the source view ever has one; the timeline view has none)."""
    text = _one_line(cue["text"])
    return f"[muted] {text}".rstrip() if cue["muted"] else text


def _write_srt(name: str, view: str, duration: float, cues: list[dict]) -> bytes:
    """``1 / 00:00:25,150 --> 00:00:26,270 / Text / blank``, numbered from 1
    in order, UTF-8 with no BOM. No header: an SRT has nowhere to put one.

    A WORDLESS sentence is left out, and the numbering stays contiguous over
    the cues written: a cue with no text is one most players skip or choke
    on. The text and JSON files keep the sentence - its timing is still a
    fact of the transcript, and there it costs nothing to record."""
    lines: list[str] = []
    number = 0
    for cue in cues:
        if not _one_line(cue["text"]):
            continue
        number += 1
        # Clamped at zero FIRST - as ``srt_time`` clamps each clock - and only
        # then floored. Floored on the raw seconds, a span before or across
        # zero satisfied the floor and was then shrunk by the two clamps to
        # nothing, or to less than the floor: the very cue the floor is for.
        # (Reachable through the whole-list Save, which does not bound a time
        # at zero; Whisper itself never writes a negative one.)
        start = max(0.0, cue["start"])
        end = max(start, cue["end"])
        if end - start < MIN_CUE_SECONDS:
            end = start + MIN_CUE_SECONDS
        lines += [str(number), f"{srt_time(start)} --> {srt_time(end)}", _marked(cue), ""]
    return "\n".join(lines).encode("utf-8")


def _write_txt(name: str, view: str, duration: float, cues: list[dict]) -> bytes:
    """A two-line header - the project and the view, then what the timings
    are - and ``[m:ss.mmm]  Text`` per sentence."""
    lines = [f"# {name} — narration ({view})", f"# {EXPORT_VIEWS[view]}", ""]
    lines += [f"[{timecode(cue['start'])}]  {_marked(cue)}".rstrip() for cue in cues]
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _write_json(name: str, view: str, duration: float, cues: list[dict]) -> bytes:
    """The project, the view, the length, the note and every sentence: its
    index in the stored transcript, its span to the transcript's own three
    decimals, its words as saved (newlines and all - JSON can carry them),
    its stored offset (null for none) and, in the source view, whether it is
    muted. The timeline view has no muted sentence, so it has no such key."""
    sentences = []
    for cue in cues:
        entry = {
            "index": cue["index"],
            "start": round(cue["start"], 3),
            "end": round(cue["end"], 3),
            "text": cue["text"],
        }
        if view == "source":
            entry["muted"] = cue["muted"]
        entry["offset"] = cue["offset"]
        sentences.append(entry)
    payload = {
        "project": name,
        "view": view,
        "duration": round(duration, 3),
        "note": EXPORT_VIEWS[view],
        "sentences": sentences,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


_WRITERS = {"srt": _write_srt, "txt": _write_txt, "json": _write_json}


def export_transcript(pid: str, fmt: str, view: str) -> tuple[str, str, bytes]:
    """The transcript as a downloadable file: ``(filename, media type, bytes)``.

    ``fmt`` is a key of :data:`EXPORT_FORMATS`, ``view`` one of
    :data:`EXPORT_VIEWS`:

    - **timeline** - the narration AS THE RE-VOICE WILL SPEAK IT: projected
      through the edit, muted, wordless and past-the-end sentences left out,
      each placed at its pin (``_timeline_cues``). Its timings are where each
      sentence is AIMED, and the file says so where it can.
    - **source** - the script as it was spoken in the source video: every
      sentence at Whisper's own times, muted ones marked (``_source_cues``);
      the SRT alone leaves a wordless sentence out (``_write_srt``).

    The file is ``<project name>-narration.<ext>`` (:data:`EXPORT_SUFFIX`),
    the name made safe the way the engine names its own outputs
    (``utils.helpers.sanitize_filename``).

    A read, like the preview and the plan: nothing on the project changes, no
    ffmpeg or ffprobe runs, and it is not a job. Raises ``ProjectNotFound``
    (404), ``SegmentNotFound`` (404, no transcript yet) or ``ValueError`` (400:
    a deck/PDF, an unknown format or view, an edit that cannot be read or
    measured).
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unknown transcript format {fmt!r}; choose one of {', '.join(EXPORT_FORMATS)}.")
    if view not in EXPORT_VIEWS:
        raise ValueError(f"Unknown transcript view {view!r}; choose one of {', '.join(EXPORT_VIEWS)}.")
    record = _record(pid)
    transcript = _transcript(record)
    if not transcript:
        raise SegmentNotFound("This video has no transcript yet - transcribe it first.")

    name = str(record.get("name") or "")
    duration, cues = (_timeline_cues if view == "timeline" else _source_cues)(record, transcript)
    media_type, extension = EXPORT_FORMATS[fmt]
    body = _WRITERS[fmt](name, view, duration, cues)
    return f"{sanitize_filename(name) or 'transcript'}{EXPORT_SUFFIX}.{extension}", media_type, body


# -- hearing one sentence ----------------------------------------------------

def _preview_narration(segment: dict, provider, voice, speed) -> tuple[str, str, float]:
    """The (provider, voice, speed) one preview runs with.

    The three arguments are the JOB's values - what the Re-voice card has
    selected - and each may be None for "whatever the studio is set to". The
    SENTENCE's own overrides win over them, exactly as they win over the job in
    ``services.processing._revoice_video``, which is what makes a preview
    "this line as the render will speak it" rather than a separate audition
    mode with its own rules.

    ``provider`` is the exception, and it is NOT taken from the sentence: the
    provider is the engine the job runs on, and nothing in the render ever reads
    a segment's stored ``provider`` - ``_revoice_video`` resolves the provider
    from the job, else the studio config, and that key exists only so
    ``effective_voice`` knows which provider the stored VOICE belongs to.
    Falling back to it here would make the preview diverge from the render in
    exactly the case that sounds most helpful: a sentence storing a Kokoro voice
    while the studio runs Edge would preview in Kokoro and render in Edge.

    The two voice paths differ on purpose, and both follow what already exists:

    - a voice asked for HERE is checked and refused when it belongs to the
      other provider (``check_voice_for_provider``, a 400) - the same answer
      generate and re-voice give a mismatched voice;
    - the voice STORED on the sentence falls back silently when it belongs to
      the other provider (``core.tts_provider.effective_voice``), because that
      is precisely what the render will do with it, and refusing it here would
      be the preview lying about the render.
    """
    from core.tts_provider import effective_voice

    # The job half, resolved exactly as the audition plan resolves it (one
    # helper, so a preview and the timeline can never disagree about what the
    # Re-voice card's three boxes mean).
    provider_id, job_voice, job_speed = _job_narration(provider, voice, speed)
    voice_id = effective_voice(segment.get("voice"), job_voice, provider_id)

    stored_speed = segment.get("speed")
    return provider_id, voice_id, job_speed if stored_speed is None else _speed(stored_speed)


def preview_segment(pid: str, index: int, *, provider=None, voice=None, speed=None) -> Path:
    """Synthesise ONE transcript sentence and return the audio file's path.

    The text is the STORED sentence, never a request body: a preview plays what
    is saved, which is why the route can be a GET (and so stays out of the audit
    guard honestly - ``tests/test_audit.py`` counts every non-GET as mutating).
    The transcript editor has an explicit Save, so "you can only hear what is
    saved" is a rule the UI can state.

    ``provider`` / ``voice`` / ``speed`` are the JOB's values - what the
    Re-voice card has selected - and any of them may be None for the studio
    default. The sentence's own ``voice`` and ``speed`` still win over them, as
    they win over the job at render time, so a parameterless call means "the
    segment's own overrides, else the studio defaults" and a call carrying the
    card's three values means "this line exactly as that re-voice would speak
    it". See ``_preview_narration`` for the one field where an asked-for value
    wins outright (the provider is the engine, not a per-sentence choice).

    Deliberately NOT a job. ``services.jobs`` allows one job per project, so a
    preview-as-job would block the very re-voice the user is auditioning for.
    It writes nothing to the project either - the only thing it leaves behind is
    a file in the shared TTS cache.

    **What it costs, and why the UI has to say so.** The first press is a
    provider round trip: measured on this machine, ~1 s per sentence against
    Edge TTS once the process is warm, and ~10 s for the very first one (the
    ``edge_tts`` import plus the first connection) - which is what
    ``PREVIEW_TIMEOUT_SECONDS`` is sized against. The second is free: the cache
    is keyed on (text, voice, speed) (``utils.helpers.get_cache_path``), which
    is the SAME key the render uses - so a preview warms the render's cache and
    a sentence auditioned here is not synthesised again when the re-voice runs.
    The flip side is that ``data/cache`` is never swept and there is no purge
    endpoint: auditioning fifty sentences across five voices leaves 250 files.
    That is a known, accepted growth path (see the porting spec's trap 19), not
    something this route should start deleting behind the render's back.

    Three hazards are handled here rather than left to the caller:

    - **the wait is bounded** (``PREVIEW_TIMEOUT_SECONDS``). Edge's own ceiling
      is 120 s and ``generate_audio`` swallows every exception and returns None,
      so a hung provider would otherwise hold a server thread for two minutes
      and surface as a 500. The synthesis runs on a daemon thread that is left
      to finish into the cache; only the waiting stops.
    - **nothing is written to the cache path in the open.** The provider is
      given a private ``.part`` file and the finished clip is published with
      ``os.replace``, so a second press cannot be served a half-written file and
      a dropped stream cannot leave a truncated entry that every later preview
      AND every later re-voice would then use as if it were whole. See
      ``_synthesise``; this is the same primitive ``save_project`` uses.
    - **Kokoro's model is probed, never downloaded** - the first Kokoro
      synthesis pulls ~340 MB, so ``KokoroModelMissing`` (409) is the answer
      when it is not on disk yet.

    One thing a preview cannot promise: a sentence with no explicit ``speed`` is
    spoken at the job's speed here, while the render may still speed it up a
    little to fit the window before the next sentence
    (``_per_sentence_speed``, capped at +30%). A sentence that carries an
    explicit speed bypasses that rule in the render too, so for that one the
    preview is exact.

    Raises ``ProjectNotFound`` (404), ``SegmentNotFound`` (404), ``ValueError``
    (400: a deck/PDF, an unknown provider, a voice from the other provider, a
    speed out of range, an empty sentence), ``KokoroModelMissing`` (409) or
    ``PreviewUnavailable`` (502).
    """
    segment = _segment(_transcript(_record(pid)), index)
    text = (segment.get("text") or "").strip()
    if not text:
        raise ValueError("This sentence has no words to speak.")
    # Truncated rather than refused: the cap exists to bound one press of a
    # button, and no real Whisper sentence reaches it.
    text = text[:MAX_PREVIEW_CHARS]

    provider_id, voice_id, speed_value = _preview_narration(segment, provider, voice, speed)

    if provider_id == "kokoro":
        from core.kokoro_tts_generator import kokoro_model_present

        if not kokoro_model_present():
            raise KokoroModelMissing(
                f"{KOKORO_MODEL_PENDING} Re-voice this video once to download it, or preview with Edge TTS."
            )

    path = _synthesise(provider_id, text, voice_id, speed_value)
    if path is None:
        from core.tts_provider import provider_display_name

        raise PreviewUnavailable(
            f"{provider_display_name(provider_id)} returned no audio for this sentence. "
            f"It may be unreachable, or '{voice_id}' may not be one of its voices - "
            "try again, or choose another voice."
        )
    return path


def cache_path_for(provider_id: str, text: str, voice_id: str, speed: float) -> Path:
    """Where a finished clip for (text, voice, speed) lives - THE SAME entry the
    render looks up, which is the whole reason a preview is free the second time
    and free again when the re-voice runs.

    The key is ``utils.helpers.get_cache_path``'s, with the one provider
    difference the engine has: Kokoro folds its active language into the voice
    half of the key (``core.kokoro_tts_generator``), because the same text in
    the same voice in another language is not the same audio. That rule is
    stated twice - here and in the generator - so ``test_narration_preview.py``
    pins this function against BOTH real generators: if either key ever changes,
    the pin fails rather than the preview quietly writing to a key nothing
    reads.

    The preview owns the key because it owns the write (see ``_synthesise``): it
    hands the provider a private temp path and publishes the result itself, so
    the cache entry is only ever created by an atomic rename of a finished file.
    """
    voice_key = voice_id
    if provider_id == "kokoro":
        from utils.config import config

        voice_key = f"{voice_id}|{config.kokoro_lang or 'en-us'}"
    return get_cache_path(text, voice_key, speed=speed, stability=0, similarity_boost=0, style=0)


def _synthesise(provider_id: str, text: str, voice_id: str, speed: float) -> Path | None:
    """One sentence through the provider, waited on for at most
    ``PREVIEW_TIMEOUT_SECONDS``. The path of the finished audio - the shared TTS
    cache entry the render will reuse - or None when the provider answered
    nothing, answered an empty file, or did not answer in time.

    **Nothing is ever written to the cache path in the open.** Left to itself,
    ``generate_audio`` with no ``output_path`` streams the provider's chunks
    straight into the shared cache entry for the whole round trip, and the only
    completeness check anywhere is that the file exists - so a second reader
    gets a truncated clip, and a stream that drops leaves a truncated clip at
    that key FOREVER (the generator's own error path returns None without
    removing it, nothing sweeps ``data/cache``, and the re-voice copies the
    entry out and checks only that it exists, so the sentence counts as a
    success and the stump is muxed into the video - straight past the
    failed-sentence counting phase 1 added). So this hands the provider a
    private ``.part`` file, exactly as the engine's five other call sites hand
    it a scratch path, and publishes it with ``os.replace`` - the same primitive
    ``services.projects.save_project`` uses against the same class of bug, and
    the same Windows retry (``replace_with_retry``). The cache path therefore
    names either nothing or a complete clip, never a fragment.

    The synthesis runs on a daemon thread of its own so the timeout is real:
    ``generate_audio`` has no timeout argument and Edge's internal ceiling is
    120 s, so the only way to stop waiting is to stop waiting. The thread keeps
    the ``.part`` file - it publishes or removes it when it eventually finishes,
    so an abandoned press still pays for the next one and leaves no litter - and
    being a daemon it cannot hold up shutdown.

    Two completeness guards, and the honest limit of them:

    - an empty result is refused rather than returned as a path to silence;
    - an empty entry ALREADY at the cache path is removed and synthesised
      again, which is as far as detecting a bad entry on read can honestly go
      here: a truncated-but-non-empty mp3 can only be told apart by decoding it,
      that decode is ffmpeg (which the re-voice already runs on its own copy,
      and which is blocked outright by the anti-malware on at least one
      development machine), and paying it on every press to guard against an
      entry this code can no longer create would be the wrong trade.
    """
    from core.tts_provider import get_tts_provider

    cache_path = cache_path_for(provider_id, text, voice_id, speed)
    if cache_path.exists():
        if cache_path.stat().st_size > 0:
            return cache_path
        # Not audio, and nothing else will ever remove it.
        cache_path.unlink(missing_ok=True)

    generator = get_tts_provider(provider_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Unique per press, so two presses of the same sentence cannot write to one
    # another's file, and neither is ever the cache entry.
    part = cache_path.with_name(f"{cache_path.stem}.{uuid.uuid4().hex[:8]}.part")
    outcome: dict = {}

    def _work() -> None:
        try:
            # ``use_cache=False``: the read above and the publish below are this
            # module's, so the generator neither looks at nor writes the cache.
            produced = generator.generate_audio(
                text=text, voice_id=voice_id, output_path=part, speed=speed, use_cache=False,
            )
            if produced and part.exists() and part.stat().st_size > 0:
                _publish(part, cache_path)
                outcome["path"] = cache_path
        except Exception as exc:  # pragma: no cover - both providers already swallow their own
            outcome["error"] = exc
        finally:
            # A no-op after a successful publish; the cleanup on every failure,
            # including one on a thread nobody is waiting for any more.
            part.unlink(missing_ok=True)

    worker = threading.Thread(target=_work, name="tts-preview", daemon=True)
    worker.start()
    worker.join(PREVIEW_TIMEOUT_SECONDS)
    if worker.is_alive():
        return None
    return outcome.get("path")


def _publish(part: Path, cache_path: Path) -> None:
    """Move a finished clip onto its cache path atomically.

    A loser of the race needs no rename: the key is derived from the text, the
    voice and the speed, so an entry already sitting there is the same audio by
    construction - whoever got there first published the very bytes this call
    would have. Only a rename that fails with nothing usable at the destination
    is a real failure.
    """
    try:
        store.replace_with_retry(part, cache_path)
    except OSError:
        if not (cache_path.exists() and cache_path.stat().st_size > 0):
            raise
