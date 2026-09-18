"""The edit: which ranges of a video project's ONE source are kept, per track.

A project gains one ordered list PER TRACK of the ranges of its source that
survive the cut, in SOURCE seconds, and nothing else::

    "edit": {"version": 2,
             "video":     {"keep": [[0.0, 47.3], [52.3, 341.008]]},
             "narration": {"keep": [[0.0, 341.008]]}}

**Absent means keep everything** - a track key absent keeps that track whole,
both absent is no edit at all - so every project made before this existed
renders exactly as it did. A split, a trim and a ripple delete are all changes
to a list; there is no clip object and no position field, because position is
*derived* (``to_timeline``) and that is what keeps the transcript's coordinates
untouched. A version-1 record (E1's one list) is read as both tracks cut
together and rewritten in this shape on the next write (spec §11.1).

**Two lists, one axis.** The VIDEO list decides what the picture shows; the
NARRATION list decides where a sentence lands and which sentences are dropped;
both close their holes from the same zero. Camtasia's lock icons are the UI of
it: cut the picture with the narration locked and every pin stays where it
was, so a sentence spoken over the removed picture plays over what follows -
the timing adjustment the owner asked for. A locked track is an untouched
list, never one re-derived from the other (traps 18 and 19).

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

# Aliased because ``narration`` is the name of a TRACK below, and the
# functions here take it as an argument.
from services import narration as sentences_service
from services import projects as store
from services import waveform

# The shape of ``record["edit"]``: 2 since E3 (one list per track); version 1
# (one list) is still READ, as cut together - see ``stored_tracks``. Bumped
# only when a record could otherwise be guessed at; E4's music lane joins
# this version additively (a missing key is no music).
VERSION = 2

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

# The two lists the record may carry (spec §11.1), in the order the payload
# and the audit name them. E4's ``music`` joins the same version additively.
TRACKS = ("video", "narration")

UNREADABLE = (
    "This project's edit was written by a different version of the app and cannot be read; "
    "clear it and cut again."
)


def _track_keep(name: str, keep, source_duration) -> list[list[float]]:
    """One track's list through :func:`validate_keep`, the refusal naming the
    track: with two lists in one body the route's 400 has to say WHICH one
    is wrong ("narration: Range 2 [...] overlaps ...")."""
    try:
        return validate_keep(keep, source_duration)
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from None


def stored_tracks(record: dict, source_duration) -> tuple[list[list[float]] | None, list[list[float]] | None]:
    """The record's edit as ``(video, narration)`` - each a validated ``keep``
    list, or ``None`` when that track keeps everything - and ``(None, None)``
    when the project has no edit at all.

    **A version-1 record is read as cut together**: ``{"version": 1, "keep":
    K}`` means ``video = narration = K`` - E1's own rule that whoever bumps
    the version reads the older shape rather than refusing it (trap 22). It
    is written back as version 2 on the next write; nothing migrates. A
    record from a version this code does not know, or a hand-edited one that
    does not validate, is a ``ValueError`` rather than a silent
    keep-everything: rendering the whole video while the user believes a cut
    is applied is the one thing this must not do quietly. Keys this version
    does not read (E4's ``music``) ride through; a version-1 ``keep`` under
    the version-2 number does not, because it could mean two things and
    "no edit" is the quiet answer to neither.
    """
    held = record.get("edit")
    if held is None:
        return None, None
    if not isinstance(held, dict):
        raise ValueError(UNREADABLE)
    version = held.get("version")
    if version == 1:
        keep = validate_keep(held.get("keep"), source_duration)
        return keep, keep
    if version != VERSION:
        raise ValueError(UNREADABLE)
    if "keep" in held:
        raise ValueError(
            "This project's edit mixes two shapes (a version-1 list under version 2) and cannot be read; "
            "clear it and cut again."
        )
    tracks: list[list[list[float]] | None] = []
    for name in TRACKS:
        track = held.get(name)
        if track is None:
            tracks.append(None)
            continue
        if not isinstance(track, dict):
            raise ValueError(f"{name}: The edit must be a list of [start, end] ranges in seconds.")
        tracks.append(_track_keep(name, track.get("keep"), source_duration))
    return tracks[0], tracks[1]


def stored_keep(record: dict, source_duration) -> list[list[float]] | None:
    """E1's name for THE PICTURE's kept ranges: the video track's list, or
    ``None`` when the picture is whole. Kept for its callers. The narration
    has a list of its own since E3 and this does not know it - a transcript
    is projected through :func:`stored_tracks`'s second list, never this one
    (trap 18)."""
    return stored_tracks(record, source_duration)[0]


@dataclass(frozen=True)
class Applied:
    """What the edit does to one transcript: the two stored lists (``None`` =
    that track keeps everything); whether the PICTURE has to be cut at all
    (``cut``: the video list removes something); whether ``sentences`` were
    projected (``projected``: the narration list removes something); the
    sentences in the coordinates the render will use; how long the output is
    - the picture's length, which is what the render's ``-shortest`` mux
    bounds everything to - and the narration track's own length beside it."""

    video: list[list[float]] | None
    narration: list[list[float]] | None
    cut: bool
    projected: bool
    sentences: list
    output_duration: float | None
    narration_duration: float | None

    @property
    def keep(self) -> list[list[float]] | None:
        """E1's name for the picture's list - what ``cut_picture`` is given."""
        return self.video


def apply(record: dict, transcript, source_duration) -> Applied:
    """The edit applied to ``transcript`` - THE function both the audition plan
    and the render call, so the two cannot disagree.

    Two lists, one axis (spec §11.2, trap 18): the NARRATION list decides
    where a sentence lands and which sentences are dropped, the VIDEO list
    decides what the picture shows, and both close their holes from the same
    zero. ``sentences`` is :func:`project_transcript` through the narration
    list iff that list removes anything; otherwise it is the transcript
    itself, untouched and unprojected - the same objects - so a project with
    no edit, a bare split and a video-only edit all hand the engine exactly
    what E1's no-edit path handed it. ``cut`` is whether the video list
    removes anything, i.e. whether the picture step runs. A locked track is
    an absent (or whole) list here and is never re-derived from the other
    (trap 19): cutting the picture with the narration untouched drops no
    sentence and moves no pin.

    An edit whose source cannot be measured any more (``source_duration``
    None: the audio removed by hand after the edit was made) is refused with
    ``SourceLengthUnknown`` rather than applied on trust or silently ignored.
    """
    video, narration = stored_tracks(record, source_duration)
    length = None if source_duration is None else round(float(source_duration), PRECISION)
    if video is None and narration is None:
        return Applied(None, None, False, False, transcript, length, length)
    if source_duration is None:
        raise SourceLengthUnknown(
            "This project has an edit but its extracted audio is missing, so the edit cannot be "
            "measured. Transcribe the video again, or clear the edit."
        )
    cut = video is not None and not whole_source(video, source_duration)
    projected = narration is not None and not whole_source(narration, source_duration)
    return Applied(
        video, narration, cut, projected,
        project_transcript(transcript, narration) if projected else transcript,
        output_duration(video) if cut else length,
        output_duration(narration) if projected else length,
    )


def _track_payload(keep, length) -> dict:
    """One track as the routes report it: its list (``None`` = everything)
    and its output's length - the source's when the track is whole, or when
    even that is unknown, ``None`` rather than a length of nothing."""
    return {"keep": keep, "output_duration": output_duration(keep) if keep else length}


def payload(video, narration, source_duration) -> dict:
    """The shape the routes answer with, and what the audition plan embeds as
    ``edit``: one block per track - its kept ranges in SOURCE seconds
    (``None`` = everything) and its output's length -, the source's length to
    :data:`PRECISION` (``None`` before transcription) and the output's, which
    is THE PICTURE's. The lengths are rounded so a client working in the
    reported coordinate space can send its last range's end straight back."""
    length = None if source_duration is None else round(float(source_duration), PRECISION)
    picture = _track_payload(video, length)
    return {
        "version": VERSION,
        "video": picture,
        "narration": _track_payload(narration, length),
        "source_duration": length,
        "output_duration": picture["output_duration"],
    }


def describe(record: dict) -> dict:
    """``GET /{pid}/edit``: each track's stored ranges (``None`` =
    everything), the source's length when it is known, and the output's. A
    version-1 record answers in the version-2 shape, cut together. Reads only."""
    source_duration = waveform.duration_for(record["id"])
    video, narration = stored_tracks(record, source_duration)
    return payload(video, narration, source_duration)


def set_edit(pid: str, video=None, narration=None) -> dict:
    """Store the given lists as the project's edit, replacing any previous
    lists - a version-1 record included, which is rewritten in this shape.

    MERGED into the stored edit rather than written from scratch: the
    version and the two tracks are this function's to write (a track given
    as ``None`` is REMOVED - it keeps everything - and a version-1 ``keep``
    never survives under version 2), and every other key rides through
    untouched, as ``stored_tracks`` promises on read - E4's ``music`` must not
    be dropped by the first cut after it lands. Both tracks ``None`` is
    refused, because an edit with no track is not an edit. Each list is
    validated BEFORE the lock and before the first write, so a refused list
    leaves the project exactly as it was. Raises ``ValueError``
    for a bad list (naming the track and the range), no track at all, or a
    deck; ``SourceLengthUnknown`` when the project has no extracted audio
    (nothing to measure against); ``ProjectNotFound`` for a project that is
    gone. The record is re-read inside ``services.projects.project_lock``
    immediately before it is written - the same lock the transcript Save and
    the narration editor take - so neither of them is overwritten with a
    stale copy.
    """
    if video is None and narration is None:
        raise ValueError("Give at least one track to cut - video, narration or both.")
    source_duration = waveform.duration_for(pid)
    if source_duration is None:
        raise SourceLengthUnknown(waveform.NO_AUDIO_MESSAGE)
    checked = {
        name: _track_keep(name, keep, source_duration)
        for name, keep in zip(TRACKS, (video, narration)) if keep is not None
    }

    with store.project_lock(pid):
        record = store.get_project(pid)
        if record is None:
            raise ProjectNotFound("Project not found.")
        if record.get("kind") not in sentences_service.NARRATION_KINDS:
            raise ValueError("Only video projects can be cut.")
        held = record.get("edit")
        merged = dict(held) if isinstance(held, dict) else {}
        merged["version"] = VERSION
        merged.pop("keep", None)
        for name in TRACKS:
            if name in checked:
                merged[name] = {"keep": checked[name]}
            else:
                merged.pop(name, None)
        record["edit"] = merged
        store.save_project(record)
    # The edit decides which sentences are spoken, and the speaking rate is
    # measured from three of those - so the memoised rate no longer describes
    # this project. Outside the lock: it guards a different thing.
    sentences_service.forget_baseline(pid)
    return payload(checked.get("video"), checked.get("narration"), source_duration)


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
        sentences_service.forget_baseline(pid)
    return payload(None, None, waveform.duration_for(pid)), cleared
