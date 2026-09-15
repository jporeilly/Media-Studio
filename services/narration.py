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

What deliberately is NOT here: anything that fits a speed automatically. See
``docs/porting/narration-timeline.md`` §9.
"""

import math
import threading
import uuid
from pathlib import Path

from services import projects as store
from services import studio_settings
from services.voices import KOKORO_MODEL_PENDING
from utils.helpers import get_cache_path

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
        return segment


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

    # An unknown provider is a ValueError (400); ``studio_voice`` is that
    # provider's configured default (Settings > Studio). Resolved from the
    # asked-for provider alone, exactly as the render resolves it.
    provider_id, studio_voice = studio_settings.resolve_narration(provider, None)
    job_voice = (
        studio_settings.check_voice_for_provider(voice, provider_id)
        if (voice or "").strip() else studio_voice
    )
    voice_id = effective_voice(segment.get("voice"), job_voice, provider_id)

    stored_speed = segment.get("speed")
    chosen_speed = _speed(stored_speed if stored_speed is not None else speed)
    return provider_id, voice_id, DEFAULT_SPEED if chosen_speed is None else chosen_speed


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
