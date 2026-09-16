"""The edit: which ranges of a video project's ONE source are kept.

A project gains an ordered list of the ranges of its source that survive the
cut - ``record["edit"] = {"version": 1, "keep": [[start, end], ...]}`` in
SOURCE seconds - and nothing else. **Absent means keep everything**, so every
project made before this existed renders exactly as it did. A split, a trim
and a ripple delete are all changes to that one list; there is no clip object
and no position field, because position is *derived* (``to_timeline``) and
that is what keeps the transcript's coordinates untouched.

**The transcript never moves.** ``services.projects.set_transcript`` decides
that two entries are the same sentence by their ``start`` and ``end``, and a
sentence's adjustments (``services.narration``) are carried across only onto a
match - so shifting every later sentence earlier after a cut would drop every
one of those adjustments by construction. The transcript therefore stays in
source seconds on disk, exactly as Whisper wrote it, and the edit is applied to
it as a PROJECTION (``project_transcript``) at plan time and at render time.
Nothing on disk changes coordinates.

**One function, two callers.** The audition plan (``services.narration.plan``)
and the render (``services.revoice``) both go through :func:`apply`, so they
cannot disagree about whether an edit changes anything or where a sentence
lands once it has. The spec is ``docs/porting/edit-timeline.md``.

**The source's length is the WAV header's** (``services.waveform.duration_for``)
- never ffprobe, which the packaged app does not have, and never
``record["duration"]``, which was measured on the video and can differ by a
frame. With no ``audio.wav`` an edit cannot be measured, so it cannot be stored
(``SourceLengthUnknown``, a 409 at the route) and it is not applied.

The arithmetic here is pure and is exercised to exhaustion by
``tests/test_edit.py``; the two store functions at the bottom take
``services.projects.project_lock`` around their read-modify-write, exactly as
every other writer of the outer ``project.json`` does.
"""

import math
from dataclasses import dataclass

from services import narration
from services import projects as store
from services import waveform

# The shape of ``record["edit"]``. Bumped when v2 (a second source, mixing)
# changes the shape, so an old record is never guessed at.
VERSION = 1

# Seconds are stored to three decimal places, the transcript's own precision
# (``services.transcription`` rounds Whisper's timestamps the same way).
PRECISION = 3

# Half a millisecond: the widest gap two 3-dp seconds can differ by and still be
# the same instant after rounding. Used to decide whether an edit really removes
# anything, so a keep-everything list written back from a rounded display is
# still keep-everything.
EPSILON = 0.0005

# The record is one JSON blob, rewritten whole and served whole, so the edit
# must stay small. A plausible edit is a few dozen ranges; a few hundred is a
# very busy one. This is a bound on abuse, not on use.
MAX_RANGES = 5000


class ProjectNotFound(LookupError):
    """No project with that id (404). The record vanished between the route's
    own check and the lock - a concurrent delete."""


class SourceLengthUnknown(ValueError):
    """The project has no extracted audio yet, so the source's length - the
    coordinate space every range is checked against - is not known (409 at the
    route; a plain refusal inside a job)."""


# -- the arithmetic ----------------------------------------------------------

def _seconds(value) -> float:
    """A stored timestamp as a float, 0.0 when it is missing or unusable, so a
    hand-edited project.json lands a sentence at the start of the video rather
    than failing the whole projection. Stricter than
    ``services.processing._seconds`` (which this otherwise mirrors, with the
    fallback fixed at 0.0): NaN and infinity are unusable here too, because a
    range comparison against either is meaningless. That helper is not
    imported because its module loads the whole media engine."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    return seconds if math.isfinite(seconds) else 0.0


def _number(value, position: int) -> float:
    """One bound of a range, checked and rounded. Bools are refused rather
    than coerced (``True`` is not one second in), and so are NaN and infinity,
    which JSON lets through and pydantic's strict floats accept."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Range {position} must be a [start, end] pair of seconds.")
    try:
        seconds = float(value)
    except OverflowError:
        # A JSON integer too large for a float passes the strict schema and
        # would be a 500 here; it is no more a second than infinity is.
        raise ValueError(f"Range {position} must be a [start, end] pair of finite seconds.") from None
    if not math.isfinite(seconds):
        raise ValueError(f"Range {position} must be a [start, end] pair of finite seconds.")
    # ``+ 0.0`` turns the -0.0 a value like -0.0004 rounds to into a plain 0.0,
    # so the stored JSON never carries a negative zero.
    return round(seconds, PRECISION) + 0.0


def validate_keep(keep, source_duration) -> list[list[float]]:
    """The ranges as they will be stored, or ``ValueError`` naming the one
    that is wrong.

    Ordered, ascending, non-overlapping, ``0 <= start < end <= source_duration``,
    every number finite and rounded to :data:`PRECISION`. Two adjacent ranges
    may touch (``[0, 10], [10, 20]``): that is a split with nothing removed,
    which the timeline draws as a boundary. An empty list is refused - an edit
    that keeps nothing has nothing to render - and so is a list long enough to
    be an attack on the record rather than an edit of it.

    ``source_duration`` is the WAV header's length (``services.waveform``).
    ``None`` skips the upper bound only; it is for reading back an edit whose
    source can no longer be measured (the audio removed by hand after the edit
    was made), never for storing one - ``set_edit`` refuses that case outright.
    """
    bound = None
    if source_duration is not None:
        try:
            bound = round(float(source_duration), PRECISION)
        except (TypeError, ValueError):
            bound = math.nan
        if not math.isfinite(bound) or bound <= 0:
            raise ValueError("The source's length is unknown, so the edit cannot be checked against it.")

    if not isinstance(keep, (list, tuple)):
        raise ValueError("The edit must be a list of [start, end] ranges in seconds.")
    if not keep:
        raise ValueError("Keep at least one range - an edit that keeps nothing has nothing to render.")
    if len(keep) > MAX_RANGES:
        raise ValueError(f"An edit is limited to {MAX_RANGES} ranges; this one has {len(keep)}.")

    checked: list[list[float]] = []
    for position, entry in enumerate(keep, start=1):
        if isinstance(entry, (str, bytes)) or not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError(f"Range {position} must be a [start, end] pair of seconds.")
        start, end = (_number(value, position) for value in entry)
        # Named to the stored precision, never ``:g``: six significant digits
        # would print 8000.001 as 8000 and the message would contradict itself
        # on a long source.
        shown = f"[{start:.3f}, {end:.3f}]"
        if start < 0:
            raise ValueError(f"Range {position} {shown} starts before 0.")
        if end <= start:
            raise ValueError(f"Range {position} {shown} must end after it starts.")
        if bound is not None and end > bound:
            raise ValueError(
                f"Range {position} {shown} runs past the end of the source, which is {bound:.3f} s long."
            )
        if checked and start < checked[-1][1]:
            previous_start, previous_end = checked[-1]
            raise ValueError(
                f"Range {position} {shown} overlaps or precedes range {position - 1} "
                f"[{previous_start:.3f}, {previous_end:.3f}]; ranges must be in ascending order and must not overlap."
            )
        checked.append([start, end])
    return checked


def output_duration(keep) -> float:
    """How long the output is: the sum of the kept ranges. Known before ffmpeg
    runs, which is what lets the mux be told its length instead of probing it."""
    return round(sum(float(end) - float(start) for start, end in keep), PRECISION)


def _walk(keep):
    """``(timeline_start, source_start, source_end)`` per range, in order."""
    at = 0.0
    for start, end in keep:
        start, end = float(start), float(end)
        yield at, start, end
        at += end - start


def to_timeline(t_source: float, keep) -> float | None:
    """Where a source moment lands in the output, or ``None`` when it falls in
    a removed range (or outside the source altogether).

    A moment exactly on a cut belongs to the join: the end of one kept range
    and the start of the next are the same output instant, so both map to it.
    """
    for at, start, end in _walk(keep):
        if start <= t_source <= end:
            return round(at + (t_source - start), PRECISION)
    return None


def to_source(t_timeline: float, keep) -> float:
    """The inverse: which source moment is showing at an output moment - for
    the playhead and the filmstrip, which seek the one source video.

    At a join the LATER range wins: the earlier range's end is the first frame
    the output never shows (``trim``'s end is exclusive), and a filmstrip or a
    playhead seeked there would show a frame that was cut. ``to_timeline``
    still maps both sides of a cut to the join, so the round trip holds.

    Clamped rather than refused at either end, because a playhead sits at
    ``output_duration`` (give or take a float) when playback finishes and that
    must show the last kept frame, not raise.
    """
    found = None
    for at, start, end in _walk(keep):
        if at <= t_timeline <= at + (end - start):
            found = round(start + (t_timeline - at), PRECISION)  # the last match is the later range
    if found is not None:
        return found
    if t_timeline < 0:
        return float(keep[0][0])
    return float(keep[-1][1])


def whole_source(keep, source_duration) -> bool:
    """Whether ``keep`` covers everything - an edit that removes nothing (a
    bare split, or the list written back from a rounded display). The render
    then skips the picture step and is byte-identical to a project with no
    edit at all. ``keep`` is assumed valid, so its ranges are disjoint and
    inside ``[0, source_duration]``, and covering everything is the same as
    adding up to it."""
    return output_duration(keep) >= round(float(source_duration), PRECISION) - EPSILON


def project_transcript(transcript, keep) -> list[dict]:
    """The sentences the render will speak, in TIMELINE seconds.

    The rule, chosen once (decision 1 of the spec's §10): **a sentence is kept
    iff its ``start`` lies inside a kept range** - ``[start, end)``, so a
    sentence beginning exactly where a cut begins is cut with it, which is what
    snapping a cut to a sentence means. A sentence whose start was removed is
    left out entirely, exactly as a muted one is: the room it occupied goes to
    the sentence before it. A kept sentence's ``end`` is clamped to its range's
    end, so nothing is ever pinned past the output.

    Every returned dict is a NEW dict - the stored transcript is never touched -
    and every other key rides through untouched: the override vocabulary
    (``services.narration.OVERRIDE_KEYS``) and anything else the segment
    carries. In particular **``offset`` is left exactly as stored**, because
    the engine applies it itself (``services.processing._pin``): only ``start``
    and ``end`` are remapped, and the pin the render uses is
    ``to_timeline(start) + offset``, not ``to_timeline(start + offset)`` - the
    latter has no answer when the offset crosses a cut, and applying the offset
    here as well would apply it twice.

    ``index`` on each returned dict is the sentence's position in the STORED
    transcript, which is what the narration routes address a sentence by; the
    projection drops sentences, so positions in the returned list are not it.
    """
    projected: list[dict] = []
    for index, seg in enumerate(transcript or []):
        if not isinstance(seg, dict):
            continue
        start = _seconds(seg.get("start"))
        held = next((r for r in keep if float(r[0]) <= start < float(r[1])), None)  # decision 1: kept iff its start is kept
        if held is None:
            continue
        end = min(max(_seconds(seg.get("end")), start), float(held[1]))
        copy = dict(seg)
        copy["index"] = index
        copy["start"] = to_timeline(start, keep)
        copy["end"] = to_timeline(end, keep)
        projected.append(copy)
    return projected


# -- the record --------------------------------------------------------------

def stored_keep(record: dict, source_duration) -> list[list[float]] | None:
    """The record's edit as a validated ``keep`` list, or ``None`` when the
    project has none (keep everything). A record from a newer version, or a
    hand-edited one that does not validate, is a ``ValueError`` rather than a
    silent keep-everything: rendering the whole video while the user believes
    a cut is applied is the one thing this must not do quietly."""
    held = record.get("edit")
    if held is None:
        return None
    if not isinstance(held, dict) or held.get("version") != VERSION:
        raise ValueError(
            "This project's edit was written by a different version of the app and cannot be read; "
            "clear it and cut again."
        )
    return validate_keep(held.get("keep"), source_duration)


@dataclass(frozen=True)
class Applied:
    """What the edit does to one transcript: the stored ranges (``None`` when
    there is no edit), whether the picture has to be cut at all, the sentences
    in the coordinates the render will use, and how long the output is."""

    keep: list[list[float]] | None
    cut: bool
    sentences: list
    output_duration: float | None


def apply(record: dict, transcript, source_duration) -> Applied:
    """The edit applied to ``transcript`` - THE function both the audition plan
    and the render call, so the two cannot disagree.

    With no edit, or one that keeps the whole source, ``sentences`` is the
    transcript itself, untouched and unprojected, and ``cut`` is False: that
    path is byte-identical to a project that never had an edit. Otherwise
    ``sentences`` is :func:`project_transcript`'s list in timeline seconds and
    ``output_duration`` is the sum of the kept ranges.

    An edit whose source cannot be measured any more (``source_duration``
    None: the audio removed by hand after the edit was made) is refused with
    ``SourceLengthUnknown`` rather than applied on trust or silently ignored.
    """
    keep = stored_keep(record, source_duration)
    length = None if source_duration is None else round(float(source_duration), PRECISION)
    if keep is None:
        return Applied(None, False, transcript, length)
    if source_duration is None:
        raise SourceLengthUnknown(
            "This project has an edit but its extracted audio is missing, so the edit cannot be "
            "measured. Transcribe the video again, or clear the edit."
        )
    if whole_source(keep, source_duration):
        return Applied(keep, False, transcript, length)
    return Applied(keep, True, project_transcript(transcript, keep), output_duration(keep))


def payload(keep, source_duration) -> dict:
    """The shape the routes answer with, and what the audition plan embeds as
    ``edit``: the kept ranges in SOURCE seconds (``None`` = everything), the
    source's length to :data:`PRECISION` (``None`` before transcription) and
    the output's. The length is rounded so a client working in the reported
    coordinate space can send its last range's end straight back."""
    length = None if source_duration is None else round(float(source_duration), PRECISION)
    return {
        "version": VERSION,
        "keep": keep,
        "source_duration": length,
        # With no edit the output is the source; when even that is unknown,
        # say so rather than claim a length of nothing.
        "output_duration": output_duration(keep) if keep else length,
    }


def describe(record: dict) -> dict:
    """``GET /{pid}/edit``: the stored ranges (``None`` = everything), the
    source's length when it is known, and the output's. Reads only."""
    source_duration = waveform.duration_for(record["id"])
    return payload(stored_keep(record, source_duration), source_duration)


def set_edit(pid: str, keep) -> dict:
    """Store ``keep`` as the project's edit, replacing any previous one.

    Validated BEFORE the lock and before the first write, so a refused list
    leaves the project exactly as it was. Raises ``SourceLengthUnknown`` when
    the project has no extracted audio (nothing to measure against),
    ``ValueError`` for a bad list or a deck, ``ProjectNotFound`` for a project
    that is gone. The record is re-read inside
    ``services.projects.project_lock`` immediately before it is written - the
    same lock the transcript Save and the narration editor take - so neither
    of them is overwritten with a stale copy.
    """
    source_duration = waveform.duration_for(pid)
    if source_duration is None:
        raise SourceLengthUnknown(waveform.NO_AUDIO_MESSAGE)
    checked = validate_keep(keep, source_duration)

    with store.project_lock(pid):
        record = store.get_project(pid)
        if record is None:
            raise ProjectNotFound("Project not found.")
        if record.get("kind") not in narration.NARRATION_KINDS:
            raise ValueError("Only video projects can be cut.")
        record["edit"] = {"version": VERSION, "keep": checked}
        store.save_project(record)
    # The edit decides which sentences are spoken, and the speaking rate is
    # measured from three of those - so the memoised rate no longer describes
    # this project. Outside the lock: it guards a different thing.
    narration.forget_baseline(pid)
    return payload(checked, source_duration)


def clear_edit(pid: str) -> tuple[dict, bool]:
    """Back to keep-everything: the key is removed, not written empty, so the
    record reads exactly as one that never had an edit. Returns the payload
    and whether there was an edit to clear, so the route can leave a no-op
    out of the audit log; nothing is written or forgotten for a no-op."""
    with store.project_lock(pid):
        record = store.get_project(pid)
        if record is None:
            raise ProjectNotFound("Project not found.")
        cleared = "edit" in record
        if cleared:
            record.pop("edit")
            store.save_project(record)
    if cleared:
        narration.forget_baseline(pid)
    return payload(None, waveform.duration_for(pid)), cleared
