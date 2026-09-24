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
- never a probe (the header is exact, spawns nothing, and is the file being
drawn), and never ``record["duration"]``, which was measured on the video and can differ by a
frame. With no ``audio.wav`` an edit cannot be measured, so it cannot be stored
(``SourceLengthUnknown``, a 409 at the route) and it is not applied.

**The music lane is different in kind** (E4, spec §12): the picture and the
narration are material with an edit - ranges of the one source - while
music is CLIPS placed on the output::

    "music": [{"id": "m3f9a1", "file": "bed.mp3", "at": 12.5, "in": 0.0,
               "out": 95.25, "gain": 0.15, "fade_in": 1.0, "fade_out": 2.0}]

``file`` names a library file (``services.music``), ``at`` is where the clip
starts in OUTPUT seconds (the picture's axis, so a cut before it moves it
with the picture when the lane is unlocked - trap 24), ``in``/``out`` the
slice of the FILE used, ``gain`` a linear factor (trap 26), the fades
seconds of linear ramp. The list joins version 2 additively - absent is no
music - and is validated against the library's recorded lengths, never
against the source. A file that has gone since the clip was placed is
reported ``missing`` on read and refused by the render (trap 25), never
rendered as silence.

The arithmetic here is pure and is exercised to exhaustion by
``tests/test_edit.py``; the two store functions at the bottom take
``services.projects.project_lock`` around their read-modify-write, exactly as
every other writer of the outer ``project.json`` does.
"""

import math
import re
from dataclasses import dataclass, field

# Aliased because ``narration`` is the name of a TRACK below, and the
# functions here take it as an argument; ``music`` likewise is the name of
# the lane's list.
from services import music as music_service
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


# -- the music lane ------------------------------------------------------------

# A clip's keys, exactly (spec §12.2): the id the client minted, the library
# file, where it starts on the output, the slice of the file, the level and
# the two fades. Nothing else is stored on a clip - ``missing`` and
# ``file_duration`` are derived on read.
CLIP_KEYS = ("id", "file", "at", "in", "out", "gain", "fade_in", "fade_out")
_CLIP_NUMBERS = ("at", "in", "out", "gain", "fade_in", "fade_out")

# Client-minted, so the selection, the undo stack and the inspector have a
# handle that survives re-ordering; the server checks it and never renumbers.
_CLIP_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# A bound on abuse, not on use: a bed and a few stings is a handful.
MAX_CLIPS = 200

# A clip shorter than this is a click, not music.
MIN_CLIP_SECONDS = 0.1

# ``set_edit``'s "leave this key exactly as it is" - one sentinel for all
# three of ``video``, ``narration`` and ``music``, because ``None`` already
# means something for each of them (a track becomes whole, the music is
# cleared). The route maps a key the body did not name to this, by
# ``model_fields_set``: absent = unchanged, null = cleared, a list = set.
UNCHANGED = object()


def _clip_number(clip: dict, key: str, label: str) -> float:
    """One of a clip's numbers, checked and rounded like a range's bound:
    bools, NaN, infinity and an integer too large for a float are refused."""
    value = clip.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: {key} must be a number.")
    try:
        number = float(value)
    except OverflowError:
        raise ValueError(f"{label}: {key} must be a finite number.") from None
    if not math.isfinite(number):
        raise ValueError(f"{label}: {key} must be a finite number.")
    return round(number, PRECISION) + 0.0


def _check_music(clips, library: dict, *, flag_missing: bool, stored=()) -> list[dict]:
    """The clips as they will be stored, sorted by ``at`` (stable), or
    ``ValueError`` naming the clip by position and id and the field.
    ``library`` is ``{name: duration}``. A file not in it is merely unbounded
    when reading back (``flag_missing`` True: the clip is kept and
    :func:`stored_music` marks it missing) and, when storing (False), a
    refusal - unless the clip is already STORED (E4c, the owner's ruling of
    2026-09-24: "you may keep what you have, you may not add what is not
    there"). ``stored`` is the record's own clips as :func:`stored_music`
    reads them; a clip whose file has gone is accepted iff a stored clip
    with the same ``id`` names the same ``file`` and the same slice (``in``
    and ``out``). Everything else about it may change - ``at`` (a move, or
    a cut's ripple), ``gain``, the fades - and is checked exactly as a live
    clip's is, the fades against the slice's own length, which is known. The
    slice cannot change because the file it slices cannot be measured: a
    longer slice of an absent file is as much "adding what is not there" as
    a new clip of it. With nothing stored every missing file is a refusal,
    exactly as before E4c."""
    if isinstance(clips, (str, bytes, dict)) or not isinstance(clips, (list, tuple)):
        raise ValueError("The music must be a list of clips.")
    if len(clips) > MAX_CLIPS:
        raise ValueError(f"The music is limited to {MAX_CLIPS} clips; this edit has {len(clips)}.")

    kept = {clip["id"]: clip for clip in stored}
    checked: list[dict] = []
    seen: dict[str, int] = {}
    for position, clip in enumerate(clips, start=1):
        if not isinstance(clip, dict):
            raise ValueError(f"music clip {position} must be an object with {', '.join(CLIP_KEYS)}.")
        ident = clip.get("id")
        label = f"music clip {position}" + (f" ({ident})" if isinstance(ident, str) and _CLIP_ID_RE.fullmatch(ident) else "")
        keys = set(clip)
        if keys != set(CLIP_KEYS):
            missing = [key for key in CLIP_KEYS if key not in keys]
            extra = sorted(keys - set(CLIP_KEYS))
            raise ValueError(
                f"{label}: " + " and ".join(
                    part for part in (
                        f"missing {', '.join(missing)}" if missing else "",
                        f"unknown {', '.join(extra)}" if extra else "",
                    ) if part
                ) + f"; a clip has exactly {', '.join(CLIP_KEYS)}."
            )
        if not isinstance(ident, str) or not _CLIP_ID_RE.fullmatch(ident):
            raise ValueError(f"{label}: id must be 1-32 characters of a-z, 0-9, _ or -.")
        if ident in seen:
            raise ValueError(f"{label}: id '{ident}' is already used by music clip {seen[ident]}.")
        seen[ident] = position
        name = clip.get("file")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{label}: file must be the name of a library file.")
        duration = library.get(name)
        # E4c: a file the library has lost is kept only under a stored id
        # that names it - a new id, or a stored id re-pointed at it, is
        # adding what is not there.
        held = kept.get(ident) if duration is None and not flag_missing else None
        if duration is None and not flag_missing and (held is None or held.get("file") != name):
            raise ValueError(f"{label}: file '{name}' is not in the library.")

        numbers = {key: _clip_number(clip, key, label) for key in _CLIP_NUMBERS}
        at, start, end, gain, fade_in, fade_out = (numbers[key] for key in _CLIP_NUMBERS)
        length = round(end - start, PRECISION)
        if held is not None:
            was_in, was_out = (round(float(held[key]), PRECISION) for key in ("in", "out"))
            if start != was_in or end != was_out:
                raise ValueError(
                    f"{label}: file '{name}' is not in the library, so its slice cannot change; "
                    f"it was {was_in:.3f}–{was_out:.3f} of the file. Move it, level it, fade it, "
                    "remove it, or put the file back under the same name."
                )
        if at < 0:
            raise ValueError(f"{label}: at ({at:.3f}) starts before 0.")
        if start < 0:
            raise ValueError(f"{label}: in ({start:.3f}) starts before 0.")
        if end <= start:
            raise ValueError(f"{label}: out ({end:.3f}) must be after in ({start:.3f}).")
        if length < MIN_CLIP_SECONDS - EPSILON:
            raise ValueError(f"{label}: the clip ({length:.3f} s from in to out) is shorter than {MIN_CLIP_SECONDS} s.")
        if duration is not None and end > round(float(duration), PRECISION) + EPSILON:
            raise ValueError(
                f"{label}: out ({end:.3f}) runs past the end of '{name}', which is {float(duration):.3f} s long."
            )
        if not 0 <= gain <= 1:
            raise ValueError(f"{label}: gain ({gain:.3f}) must be between 0 and 1.")
        if fade_in < 0 or fade_out < 0:
            raise ValueError(f"{label}: fade_in and fade_out must be 0 s or more.")
        if fade_in + fade_out > length + EPSILON:
            raise ValueError(
                f"{label}: fade_in + fade_out ({fade_in + fade_out:.3f} s) is longer than the clip ({length:.3f} s)."
            )
        checked.append({
            "id": ident, "file": name, "at": at, "in": start, "out": end,
            "gain": gain, "fade_in": fade_in, "fade_out": fade_out,
        })
    checked.sort(key=lambda clip: clip["at"])  # stable: equal starts keep their order
    return checked


def validate_music(clips, library: dict, *, stored=()) -> list[dict]:
    """The clips as they will be stored, or ``ValueError`` naming the one
    that is wrong: a list of at most :data:`MAX_CLIPS` objects with exactly
    :data:`CLIP_KEYS`; ``id`` matching ``^[a-z0-9_-]{1,32}$`` and unique;
    ``file`` in ``library`` (``{name: duration}``) - or, with ``stored``
    given (the record's clips as :func:`stored_music` reads them), a file
    the library has lost under a stored id that names it with the same
    ``in`` and ``out`` (E4c: you may keep what you have, you may not add
    what is not there); the numbers finite, not bool, rounded to
    :data:`PRECISION`; ``at >= 0``; ``0 <= in < out <=`` the file's length
    with ``out - in >= MIN_CLIP_SECONDS``; ``0 <= gain <= 1``; the fades
    ``>= 0`` and together no longer than the clip. Sorted by ``at``.
    Overlapping clips are allowed and sum (two beds cross-fading by hand is
    the ordinary use). With no ``stored`` clips a missing file is always a
    refusal."""
    return _check_music(clips, library, flag_missing=False, stored=stored)


def _annotated(clip: dict, library: dict) -> dict:
    """A stored clip as it is read back: with the library's length for its
    file and whether the file has gone (``missing``), which the plan's
    ``edit`` block carries to the lane and the render refuses on."""
    duration = library.get(clip["file"])
    return {
        **clip,
        "file_duration": None if duration is None else round(float(duration), PRECISION),
        "missing": duration is None,
    }


def stored_music(record: dict, library: dict | None = None) -> list[dict]:
    """The record's music clips as read back - each with ``file_duration``
    and ``missing`` - or ``[]`` when the edit has none. ``library`` is
    ``{name: duration}``; ``None`` reads the library's index, and only when
    there are clips to check against it, so a project without music never
    touches the library.

    A list that does not validate in SHAPE is a ``ValueError`` naming the
    clip, as an unreadable track list is (E1's rule: never a silent
    keep-everything). A file that is no longer in the library is NOT an
    error here (trap 25): the clip comes back with ``missing: True`` - the
    lane draws it hatched, the audition skips it, the render refuses - and
    its ``in``/``out`` cannot be bounded, so they are not."""
    held = record.get("edit")
    if held is None:
        return []
    if not isinstance(held, dict):
        raise ValueError(UNREADABLE)
    clips = held.get("music")
    if clips is None:
        return []
    if library is None:
        library = music_service.library()
    try:
        checked = _check_music(clips, library, flag_missing=True)
    except ValueError as exc:
        raise ValueError(
            f"This project's music cannot be read ({exc}); clear the edit and place the clips again."
        ) from None
    return [_annotated(clip, library) for clip in checked]


# -- the record --------------------------------------------------------------

# The two lists the record may carry (spec §11.1), in the order the payload
# and the audit name them. E4's ``music`` joins the same version additively
# and is not a track: it has no kept list, and ``stored_music`` reads it.
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
    bounds everything to - and the narration track's own length beside it;
    and ``music``, the clips as :func:`stored_music` reads them (each with
    ``file_duration`` and ``missing``), ``[]`` when there are none."""

    video: list[list[float]] | None
    narration: list[list[float]] | None
    cut: bool
    projected: bool
    sentences: list
    output_duration: float | None
    narration_duration: float | None
    music: list = field(default_factory=list)

    @property
    def keep(self) -> list[list[float]] | None:
        """E1's name for the picture's list - what ``cut_picture`` is given."""
        return self.video


def apply(record: dict, transcript, source_duration, library: dict | None = None) -> Applied:
    """The edit applied to ``transcript`` - THE function both the audition plan
    and the render call, so the two cannot disagree.

    ``library`` is the music library as ``{name: duration}`` for
    :func:`stored_music`; ``None`` (every real caller) reads the index, and
    only when the record has clips. The music rides along on ``music`` and
    changes nothing else here: it is placed on the output the tracks make.

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
    music = stored_music(record, library)
    length = None if source_duration is None else round(float(source_duration), PRECISION)
    if video is None and narration is None:
        return Applied(None, None, False, False, transcript, length, length, music)
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
        music,
    )


def _track_payload(keep, length) -> dict:
    """One track as the routes report it: its list (``None`` = everything)
    and its output's length - the source's when the track is whole, or when
    even that is unknown, ``None`` rather than a length of nothing."""
    return {"keep": keep, "output_duration": output_duration(keep) if keep else length}


def payload(video, narration, source_duration, music=()) -> dict:
    """The shape the routes answer with, and what the audition plan embeds as
    ``edit``: one block per track - its kept ranges in SOURCE seconds
    (``None`` = everything) and its output's length -, the music clips as
    read back (each with ``file_duration`` and ``missing``; ``[]`` when there
    are none), the source's length to :data:`PRECISION` (``None`` before
    transcription) and the output's, which is THE PICTURE's. The lengths are
    rounded so a client working in the reported coordinate space can send its
    last range's end straight back. ``music`` is what :func:`stored_music`
    returns; a clip without the two derived keys is given them."""
    length = None if source_duration is None else round(float(source_duration), PRECISION)
    picture = _track_payload(video, length)
    return {
        "version": VERSION,
        "video": picture,
        "narration": _track_payload(narration, length),
        "music": [
            {**clip, "file_duration": clip.get("file_duration"), "missing": bool(clip.get("missing"))}
            for clip in music
        ],
        "source_duration": length,
        "output_duration": picture["output_duration"],
    }


def describe(record: dict) -> dict:
    """``GET /{pid}/edit``: each track's stored ranges (``None`` =
    everything), the music clips, the source's length when it is known, and
    the output's. A version-1 record answers in the version-2 shape, cut
    together. Reads only."""
    source_duration = waveform.duration_for(record["id"])
    video, narration = stored_tracks(record, source_duration)
    return payload(video, narration, source_duration, stored_music(record))


def set_edit(pid: str, video=UNCHANGED, narration=UNCHANGED, music=UNCHANGED) -> tuple[dict, bool]:
    """Store the given lists as the project's edit, and answer with the edit
    as it now stands and whether anything was given to change.

    **One rule for all three keys**: :data:`UNCHANGED` (the default, and what
    the route passes for a key the body did not name) leaves that key exactly
    as it is; ``None`` clears it - a track back to whole, the music gone -;
    a list is validated and stored. ``[]`` clears the music too (no clips is
    no music) while an empty track list is a refusal (E1's rule: keep at
    least one range). So a cut sends its tracks and no ``music`` and never
    drops the clips, and a clip commit sends ``music`` and no tracks and
    never drops the picture's cut.

    MERGED into the stored edit rather than written from scratch: the
    version, the two tracks and the music are this function's to write, and
    every other key rides through untouched, as ``stored_tracks`` promises on
    read. A version-1 record is read as both tracks cut together (§11.1) and
    rewritten in the version-2 shape here, so that cut survives a call that
    names neither track. When the merge leaves nothing but the version - no
    track list and no music - the ``edit`` key is REMOVED, as ``clear_edit``
    removes it, so a record never carries an edit that is no edit.

    A call that names nothing at all is a NO-OP: nothing is written, nothing
    is forgotten, and the answer is the edit as it stands with ``False``
    beside it, so the route leaves it out of the audit log exactly as it
    leaves out a ``DELETE`` that had nothing to clear.

    Every list is validated BEFORE the first write - the given lists before
    the lock, the merged edit inside it and before ``save_project`` - so a
    refused list, and a stored one this version cannot read, leave the
    project exactly as it was. Raises ``ValueError`` for a bad list (naming
    the track and the range, or the clip and the field), an unreadable stored
    edit, or a deck; ``SourceLengthUnknown`` when a TRACK list is given and
    the project has no extracted audio (nothing to measure it against - the
    music is measured against the library, so a music-only call needs none);
    ``ProjectNotFound`` for a project that is gone. The record is re-read
    inside ``services.projects.project_lock`` immediately before it is
    written - the same lock the transcript Save and the narration editor
    take - so neither of them is overwritten with a stale copy.

    **A music list is checked against the record's STORED clips** (E4c):
    a clip whose file the library has lost is accepted iff the record
    already holds it - same id, same file, same slice - so a lost file no
    longer freezes the lane; see :func:`validate_music`. "Stored" is what
    the record holds at write time, so the list is checked against the
    record read before the lock (a refused list writes nothing) and AGAIN
    against the record re-read inside it, which is the one being written
    - a commit that landed in between may have changed what is stored. A
    stored music list this version cannot read is :func:`stored_music`'s
    error on either read; clearing the music (``[]`` or ``None``) reads
    nothing and is the way out that wording names.
    """
    given = {name: value for name, value in zip(TRACKS, (video, narration)) if value is not UNCHANGED}
    if not given and music is UNCHANGED:
        record = store.get_project(pid)
        if record is None:
            raise ProjectNotFound("Project not found.")
        if record.get("kind") not in sentences_service.NARRATION_KINDS:
            raise ValueError("Only video projects can be cut.")
        return describe(record), False
    source_duration = waveform.duration_for(pid)
    if source_duration is None and any(keep is not None for keep in given.values()):
        raise SourceLengthUnknown(waveform.NO_AUDIO_MESSAGE)
    checked = {
        name: _track_keep(name, keep, source_duration)
        for name, keep in given.items() if keep is not None
    }
    library = None
    clips = UNCHANGED
    if music is None or (not isinstance(music, (str, bytes, dict)) and hasattr(music, "__len__") and len(music) == 0):
        clips = None
    elif music is not UNCHANGED:
        library = music_service.library()
        record = store.get_project(pid)
        if record is None:
            raise ProjectNotFound("Project not found.")
        clips = validate_music(music, library, stored=stored_music(record, library))

    with store.project_lock(pid):
        record = store.get_project(pid)
        if record is None:
            raise ProjectNotFound("Project not found.")
        if record.get("kind") not in sentences_service.NARRATION_KINDS:
            raise ValueError("Only video projects can be cut.")
        if clips is not None and clips is not UNCHANGED:
            # Against the record being written, not the one read before the
            # lock: what is stored decides which missing files may stay.
            clips = validate_music(music, library, stored=stored_music(record, library))
        held = record.get("edit")
        merged = dict(held) if isinstance(held, dict) else {}
        if merged.get("version") == 1 and "keep" in merged:
            # Version 1 means both tracks cut together (``stored_tracks``);
            # written back in this shape here, so a call that leaves a track
            # alone keeps that cut rather than dropping a list it never saw.
            together = merged.pop("keep")
            merged["video"] = {"keep": together}
            merged["narration"] = {"keep": together}
        merged["version"] = VERSION
        merged.pop("keep", None)  # never under version 2: it could mean two things
        for name in TRACKS:
            if name in checked:
                merged[name] = {"keep": checked[name]}
            elif name in given:  # given as None: the track keeps everything again
                merged.pop(name, None)
        if clips is None:
            merged.pop("music", None)
        elif clips is not UNCHANGED:
            merged["music"] = clips
        # What the merged edit READS BACK as, before anything is written: a
        # stored list this version cannot read (a hand-edited record, a
        # foreign one) refuses the call instead of landing the cut and then
        # raising on the way out with the write already done.
        held_video, held_narration = stored_tracks({"edit": merged}, source_duration)
        held_music = stored_music({"edit": merged}, library)
        if any(key in merged for key in (*TRACKS, "music")):
            record["edit"] = merged
        else:
            # Nothing left to describe: the record reads as one that never
            # had an edit, as it does after ``clear_edit``.
            record.pop("edit", None)
        store.save_project(record)
    # The edit decides which sentences are spoken, and the speaking rate is
    # measured from three of those - so the memoised rate no longer describes
    # this project. Outside the lock: it guards a different thing. (A change
    # to the music alone moves no sentence; forgetting is still harmless.)
    sentences_service.forget_baseline(pid)
    return payload(held_video, held_narration, source_duration, held_music), True


def clear_edit(pid: str) -> tuple[dict, bool]:
    """Back to keep-everything: the key is removed, not written empty - the
    music with it, since it is "back to no edit" - so the record reads exactly
    as one that never had an edit. Returns the payload and whether there was
    an edit to clear, so the route can leave a no-op out of the audit log;
    nothing is written or forgotten for a no-op."""
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
