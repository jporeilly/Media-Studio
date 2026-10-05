"""T1: the deck render - ffmpeg composes the picture from stills.

What these tests hold in place:

- **Timing.** Segment boundaries are rounded to frames CUMULATIVELY, so over
  hundreds of odd durations no boundary is more than half a frame from its
  exact time (per-segment rounding drifts by many frames); the LAST boundary
  rounds up, so the picture never ends before the narration does (read back
  from a 2 fps render: the narration's last 0.2 s is in the file). The
  picture's timeline follows the master track: the intro card is the track's
  leading silence, each slide its span on the track, each pause the gap the
  track REALLY left after it (``_build_master_audio(gaps_out=...)``: the
  transition sound's length after a narrated slide when it is longer than the
  pause, the pause after a silent one).
- **A fade-in on either side of the frame grid.** ffmpeg's ``fade`` never
  fades when the first timestamp it sees is negative, and a slide whose start
  rounds DOWN to a frame starts before its own zero. No transition chain's
  clock goes below zero (the graphs, for every kind and for animated slides),
  and on every real ffmpeg each fading kind - fade-to-black, crossfade,
  fade-to-white, zoom-in - opens dark (white), half way at half way, for a
  still and an animated slide whose start rounds down AND for ones whose
  start rounds up.
- **The watermark on its own pixel.** ``overlay`` snaps an odd corner down
  to even in 4:2:0; the mark's still is padded so it lands where it was
  placed, read back pixel by pixel against a reference composite on a dark
  slide at an odd position.
- **The transitions as planned and as drawn.** Which slide carries which
  effect (none on the first slide's opening or the last's close); then, on
  every real ffmpeg on the machine (the dev box's and the bundled 7.1 - the
  skip rule of ``test_generation_options``), a synthetic three-slide deck -
  two stills and a short animated clip, a title card and a watermark -
  rendered once per transition kind and read back: the encode, the frame
  count against the master track, mid-slide equal to the slide, mid-fade
  between the slide and black (or WHITE, for fade-to-white, which now fades
  through white), each slide direction's half-way frame entering from the
  edge its name says, the zoom darker and enlarged half way, the watermark
  corner blended as moviepy blended it, the card present, the animated slide
  looped over its span and moving, and the narration at its SOURCE level
  (the unity up-mix: within 0.5 dB of the master track, never the -3 dB
  rematrix moviepy's reader applied).
- **Chunks.** No chunk holds more segments than its limit (so a 60-slide
  deck needs no more memory than a 20-slide one), none but a whole deck is
  shorter than ``MIN_CHUNK_FRAMES``, and a deck rendered in several chunks
  joined by a stream copy has exactly the frames of the same deck rendered
  in one.
- **The sound.** The graph's shape (unity ``pan``, 48 kHz, a bounded
  ``apad``, the music's volume, fades and loop, ``amix`` with
  ``normalize=0``), and on the real binary the playlist in order, looped as a
  whole, faded in and out, under the narration at its level.
- **Cancel, progress and failure.** A cancel mid-encode stops ffmpeg within a
  second or so and leaves no output, no partial file and no scratch; the
  progress reported before it never goes back (its first report may be 0).
  A render in several chunks reports frames done of the whole render. A join
  that fails, or is cancelled while it writes, leaves no partial video; a
  sound encode that fails is reported as one.
"""

import json
import logging
import math
import random
import re
import subprocess
import sys
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

import utils.config as config_module
from core import video_creator
from core.video_creator import DeckSegment, SlideClipInfo, VideoCreator
from utils.config import config

from test_chapters import _ffprobe_beside
from test_generation_options import REAL_FFMPEGS, needs_ffmpeg

KINDS = ["none", "fade-to-black", "fade-to-white", "crossfade", "slide-left", "slide-right",
         "slide-up", "slide-down", "zoom-in"]
ON_EVERY_FFMPEG = pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(config_module, "TEMP_DIR", tmp_path / "temp")


# ── timing, without a binary ──────────────────────────────────────────────────

def test_round_frames_rounds_half_up():
    assert video_creator.round_frames(1.25, 2) == 3, "2.5 frames is frame 3, not banker's 2"
    assert video_creator.round_frames(1.24, 2) == 2
    assert video_creator.round_frames(0.0, 24) == 0
    assert video_creator.round_frames(10 / 24, 24) == 10


def test_frame_boundaries_round_the_cumulative_time_so_no_boundary_drifts():
    """Hundreds of odd durations (thirds, sevenths, half-frame edges): every
    boundary is within half a frame of the exact cumulative time, at every
    rate. Rounding each segment on its own - the obvious way - drifts by many
    frames over the same list, so the property is not a trivial one."""
    rng = random.Random(20261001)
    durations = [rng.uniform(0.04, 30.0) for _ in range(400)]
    durations += [1 / 3, 2 / 7, 0.5 / 24, 1.5 / 24, 0.2499, 0.2501, 0.75, 13 / 11] * 20
    exact = [0.0]
    for seconds in durations:
        exact.append(math.fsum([exact[-1], seconds]))
    for fps in (2, 24, 25, 30):
        bounds = video_creator.frame_boundaries(durations, fps)
        assert len(bounds) == len(durations) + 1 and bounds[0] == 0
        # Every boundary BETWEEN segments; the last one rounds up (the next test).
        worst = max(abs(b - t * fps) for b, t in zip(bounds[:-1], exact[:-1]))
        assert worst <= 0.5 + 1e-6, f"a boundary is {worst:.3f} frames from its time at {fps} fps"
        assert 0 <= bounds[-1] - exact[-1] * fps < 1 + 1e-6, "the end: never before the track, under a frame after"
        per_segment = [0]
        for seconds in durations:
            per_segment.append(per_segment[-1] + round(seconds * fps))
        assert max(abs(b - t * fps) for b, t in zip(per_segment, exact)) > 2, "the contrast"


def test_the_last_boundary_rounds_up_so_the_picture_never_ends_before_the_track():
    """The picture's end is the track's end rounded UP to a frame: rounded to
    the nearest one, a 2.2 s narration at 2 fps got 4 frames (2.0 s) and lost
    its last 0.2 s. Boundaries between segments still round to the nearest
    frame, and a total that is a whole number of frames gains nothing."""
    assert video_creator.frame_boundaries([2.2], 2) == [0, 5], "2.5 s of picture for 2.2 s of track"
    assert video_creator.frame_boundaries([2.0], 2) == [0, 4], "a whole number of frames: nothing added"
    assert video_creator.frame_boundaries([1.0, 1.24, 0.1], 2) == [0, 2, 4, 5], "2.34 s: 5 frames; 2.24 s rounds to 4"
    assert video_creator.frame_boundaries([0.1] * 30, 2) == [round(0.1 * k * 2 + 1e-9) for k in range(30)] + [6], (
        "3.0 s to within float noise is 6 frames, not 7")
    assert video_creator.frame_boundaries([], 24) == [0]
    rng = random.Random(7)
    for _ in range(300):
        durations = [rng.uniform(0.05, 20.0) for _ in range(rng.randint(1, 12))]
        for fps in (2, 24):
            end = video_creator.frame_boundaries(durations, fps)[-1]
            total = math.fsum(durations)
            assert end / fps >= total - 1e-6 / fps, "never before the track"
            assert end / fps < total + 1 / fps, "and less than a frame after it"


def _clips(n=3, **kwargs):
    return [SlideClipInfo(slide_index=i, image_path=Path(f"slide_{i}.png"), **kwargs) for i in range(n)]


def test_the_timeline_follows_the_master_track():
    """The intro card is the track's leading silence (whole milliseconds),
    each slide its span on the track, each pause the gap the track left
    after it - whatever the pause setting says - and the outro card its own
    length. A pause is white for fade-to-white."""
    creator = VideoCreator(intro_text="Hi", intro_duration=2.5, outro_text="Bye", outro_duration=1.5,
                           transition_pause=1.0, slide_transition="fade-to-white")
    clips = _clips()
    clips[1].pause_after = 3.0
    segments = creator.deck_segments(clips, [2.0, 3.5, 4.25], gaps=[1.0, 3.2, 0.0])
    assert [(s.kind, s.seconds) for s in segments] == [
        ("card", 2.5), ("slide", 2.0), ("pause", 1.0), ("slide", 3.5), ("pause", 3.2), ("slide", 4.25), ("card", 1.5),
    ], "the 3.2 s gap is the track's (a transition sound longer than the 3 s pause), not the pause setting"
    assert {s.color for s in segments if s.kind == "pause"} == {"white"}
    assert segments[0].card == ("Hi", "") and segments[-1].card == ("Bye", "")
    assert [s.image for s in segments if s.kind == "slide"] == [c.image_path for c in clips]

    # No track at all (no slide has narration): each slide's own length or
    # 5 s, and the pause in whole milliseconds, as the track would have had it.
    creator = VideoCreator(transition_pause=0.3333)
    segments = creator.deck_segments(_clips(2), [], gaps=[])
    assert [(s.kind, s.seconds) for s in segments] == [("slide", 5.0), ("pause", 0.333), ("slide", 5.0)]
    assert segments[1].color == "black"


class _Seg:
    """A pydub.AudioSegment stand-in: lengths, overlay and slicing."""

    def __init__(self, ms=0):
        self.ms = ms

    @classmethod
    def silent(cls, duration=0):
        return cls(duration)

    @classmethod
    def empty(cls):
        return cls(0)

    @classmethod
    def from_file(cls, path):
        return cls(2500 if "sound" in str(path) else 1500)

    def __add__(self, other):
        return _Seg(self.ms + other.ms)

    def __len__(self):
        return self.ms

    def overlay(self, other):
        return self

    def __getitem__(self, cut):
        return _Seg(len(range(*cut.indices(self.ms))))

    def export(self, path, **kwargs):
        Path(path).write_text(str(self.ms))


def test_the_master_track_reports_the_gap_it_left_after_each_slide(tmp_path, monkeypatch):
    """Today's transition-sound case, as the picture now follows it: after a
    NARRATED slide the track's gap is the longer of the pause and the sound;
    after a SILENT slide it is the pause alone (the old picture held its pause
    clip for the sound's length there, drifting from the track); after the
    last slide there is none."""
    fake = types.ModuleType("pydub")
    fake.AudioSegment = _Seg
    monkeypatch.setitem(sys.modules, "pydub", fake)
    monkeypatch.setattr(video_creator, "_trim_leading_silence", lambda path, profile=None: path)
    monkeypatch.setattr(video_creator, "_level_opening", lambda audio, profile=None: audio)
    config._config["tts_provider"] = "edge_tts"
    sound = tmp_path / "sound.wav"
    sound.write_bytes(b"wav")
    clips = []
    for i, narrated in enumerate((True, False, True, True)):
        audio = None
        if narrated:
            audio = tmp_path / f"slide_{i}.mp3"
            audio.write_bytes(b"mp3")
        clips.append(SlideClipInfo(slide_index=i, audio_path=audio, duration=2.0))
    clips[2].pause_after = 3.0

    gaps: list = []
    master, durations = video_creator._build_master_audio(
        clips, voice_start_delay=0.0, transition_pause=1.0, transition_sound_path=sound, gaps_out=gaps)
    try:
        assert gaps == [2.5, 1.0, 3.0, 0.0], "sound 2.5 s > pause 1 s; silent: the pause; 3 s pause > sound; last: none"
        assert int(master.read_text()) == round(1000 * (sum(durations) + sum(gaps)))
    finally:
        master.unlink(missing_ok=True)


@pytest.mark.parametrize("kind", KINDS)
def test_each_transition_is_planned_on_the_right_slides(kind):
    """The first slide never opens with a transition and the last never
    closes with one; the fades close as well as open, the slides and the zoom
    only open; the transition lasts min(duration, slide / 3)."""
    creator = VideoCreator(slide_transition=kind, transition_duration=0.8)
    segments = [s for s in creator.deck_segments(_clips(3), [6.0, 1.5, 6.0], gaps=[0.5, 0.5, 0.0]) if s.kind == "slide"]
    opening = {"fade-to-black": "fade", "fade-to-white": "fade", "crossfade": "fade", "zoom-in": "zoom",
               "slide-left": "slide-from-right", "slide-right": "slide-from-left",
               "slide-up": "slide-from-bottom", "slide-down": "slide-from-top"}.get(kind, "")
    closing = "fade" if kind in video_creator.FADE_COLOURS else ""
    assert [(s.effect_in, s.effect_out) for s in segments] == [("", closing), (opening, closing), (opening, "")]
    if kind != "none":
        assert [s.effect_seconds for s in segments] == [0.8, 0.5, 0.8], "min(0.8, 1.5 / 3) on the short slide"
    assert {s.effect_color for s in segments} == ({"white"} if kind == "fade-to-white" else {"black"})
    # A zero duration is no transition at all - the gate fps_for_transition uses.
    zero = VideoCreator(slide_transition=kind, transition_duration=0.0)
    assert all(not s.effect_in and not s.effect_out for s in zero.deck_segments(_clips(3), [2.0] * 3, [0.5] * 3))


def test_deck_fps_raises_an_animated_deck_to_the_transition_rate():
    assert video_creator.deck_fps("none", 0.5) == video_creator.STATIC_FPS
    assert video_creator.deck_fps("none", 0.5, animated=True) == video_creator.TRANSITION_FPS
    assert video_creator.deck_fps("crossfade", 0.5) == video_creator.TRANSITION_FPS
    assert video_creator.deck_fps("crossfade", 0.0, animated=False) == video_creator.STATIC_FPS


# ── chunks, without a binary ──────────────────────────────────────────────────

def _bounds_of(seconds, fps):
    return video_creator.frame_boundaries(seconds, fps)


def test_chunks_hold_at_most_their_limit_and_are_never_shorter_than_the_minimum():
    deck = [20.0, 0.5] * 59 + [20.0]                     # 60 slides with their pauses
    for fps in (2, 24):
        bounds = _bounds_of(deck, fps)
        chunks = video_creator.deck_chunks(bounds, 16)
        assert chunks[0][0] == 0 and chunks[-1][1] == len(deck)
        assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:])), "back to back, nothing twice"
        assert all(last - first <= 16 for first, last in chunks)
        assert all(bounds[last] - bounds[first] >= video_creator.MIN_CHUNK_FRAMES for first, last in chunks)
    # Tiny segments: a chunk grows past its limit only until it is long enough.
    bounds = _bounds_of([0.5] * 100, 2)                  # one frame each
    chunks = video_creator.deck_chunks(bounds, 4)
    assert all(bounds[last] - bounds[first] >= 48 for first, last in chunks)
    assert max(last - first for first, last in chunks) <= 48 + 47, "bounded however small the segments"
    # A short tail is folded into the chunk before it; a short deck is one chunk.
    bounds = _bounds_of([30.0] * 5 + [0.5], 2)
    assert video_creator.deck_chunks(bounds, 5) == [(0, 6)]
    assert video_creator.deck_chunks(_bounds_of([1.0, 1.0], 2), 1) == [(0, 2)]


def test_a_60_slide_deck_needs_no_bigger_chunks_than_a_20_slide_one():
    """Memory is spent per chain in a chunk (ffmpeg primes every chain of a
    graph before its first frame), so the largest chunk is what a deck costs:
    the same for 20 slides and for 60."""
    sizes = {}
    for slides in (20, 60):
        deck = [22.0, 0.5] * (slides - 1) + [22.0]
        chunks = video_creator.deck_chunks(_bounds_of(deck, 24), video_creator.chunk_limit((1920, 1080)))
        sizes[slides] = max(last - first for first, last in chunks)
    assert sizes[60] == sizes[20] == 8
    assert video_creator.chunk_limit((1920, 1080)) == 8
    assert video_creator.chunk_limit((3840, 2160)) == 4
    assert video_creator.chunk_limit((1280, 720)) == 18
    assert video_creator.chunk_limit((7680, 4320)) == video_creator.MIN_CHUNK_SEGMENTS


def test_render_timeout_scales_with_frames_and_size():
    assert video_creator.render_timeout(0, (1920, 1080)) == video_creator.RENDER_BASE_SECONDS
    assert video_creator.render_timeout(10_000, (1920, 1080)) == pytest.approx(120 + 500)
    assert video_creator.render_timeout(10_000, (3840, 2160)) == pytest.approx(120 + 2000)
    assert video_creator.render_timeout(10_000, (1280, 720)) == pytest.approx(620), "never less than 1080p's"


# ── the graphs, without a binary ──────────────────────────────────────────────

def test_the_sound_graph_is_unity_48k_and_bounded():
    """The narration up-mixed with the re-voice mux's own filter (never the
    -3 dB rematrix), at 48 kHz, padded to the picture with a BOUNDED apad."""
    graph = video_creator.deck_audio_graph(3, [], 443.0, 0.25, 2.0, False)
    assert graph == "[3:a]pan=stereo|FL=FL+FC|FR=FR+FC,aresample=48000,apad=whole_dur=443.000000,atrim=end=443.000000[a]"
    assert video_creator.UPMIX_STEREO in graph and "aformat=channel_layouts" not in graph


def test_the_music_graph_is_the_old_playlist_behaviour():
    """Tracks in order, looped as a whole when shorter than the video, a
    linear volume, one fade in at the start and one out at the end, never
    ducked, summed under the voice with normalize=0."""
    graph = video_creator.deck_audio_graph(2, [3, 4], 60.0, 0.25, 2.0, True)
    assert "[3:a]pan=stereo|FL=FL+FC|FR=FR+FC,aresample=48000[mu0]" in graph
    assert "[mu0][mu1]concat=n=2:v=0:a=1[playlist]" in graph
    assert ("[playlist]aformat=sample_fmts=s16,aloop=loop=-1:size=2147483647,atrim=end=60.000000,volume=0.250000,"
            "afade=t=in:st=0:d=2.000000,afade=t=out:st=58.000000:d=2.000000[bed]") in graph
    assert graph.endswith("[voice][bed]amix=inputs=2:duration=first:normalize=0[a]")
    assert "sidechain" not in graph and "dynaudnorm" not in graph, "no ducking"
    one = video_creator.deck_audio_graph(2, [3], 60.0, 0.25, 2.0, False)
    assert "aloop" not in one and "concat" not in one, "a single track loops by -stream_loop"
    short = video_creator.deck_audio_graph(None, [0], 3.0, 0.25, 2.0, False)
    assert "afade" not in short and short.endswith("[a]"), "no fades when the video is not longer than both"


def test_the_chunk_graph_draws_a_transition_on_the_slides_own_time():
    """The transition's frames are split off the still and re-timed so 0 is
    the slide's EXACT start on the track, not the frame it was rounded to."""
    seg = DeckSegment(2.0, image="s0.png", effect_in="slide-from-right", effect_out="",
                      effect_seconds=0.5, effect_color="black")
    pause = DeckSegment(0.51, kind="pause")
    segments = [pause, seg]
    bounds = video_creator.frame_boundaries([0.51, 2.0], 24)     # the slide starts at 12.24 frames -> 12
    inputs, graph, count = video_creator.deck_chunk_graph(segments, bounds, [0.0, 0.51], 0, 2, 24, (640, 360))
    assert inputs == ["-i", "s0.png"] and count == 1
    assert "split=2" in graph
    assert "setpts=(N+-0.240000)/24/TB+1/TB" in graph, (
        "frame 0 of the slide is 0.24 frames before its start, on a clock shifted a second so it is never negative")
    assert ("pad=w=1280:h=360:x=640:y=0:color=black,"
            "crop=w=640:h=360:x='640-trunc(640*(1-clip((t-1)/0.500000,0,1)))'") in graph
    assert "loop=loop=12:size=1" in graph and "loop=loop=35:size=1" in graph, (
        "13 transition frames (the 13th is at 0.49 s of the slide) + 36 = 49 frames: 2.51 s ends on frame 61, "
        "rounded up")
    assert "color=c=black:s=640x360:r=24" in graph and "trim=end_frame=12" in graph


_CLOCK = re.compile(r"setpts=\(N\+(-?[\d.]+)\)/([\d.]+)/TB\+([\d.]+)/TB")


def test_no_transition_chain_ever_sees_a_negative_timestamp():
    """ffmpeg's ``fade`` never fades when the first timestamp it sees is
    negative, and a slide whose start rounds DOWN to a frame has its first
    frame before its exact start. So every transition chain - still parts and
    animated slides, every kind - runs on a clock shifted by
    ``transition_clock_shift``: the first timestamp of each is at or above
    zero, the fade-in starts at the shift and the fade-out at the slide's
    length less the fade plus the shift."""
    assert video_creator.transition_clock_shift(24) == 1.0 and video_creator.transition_clock_shift(2) == 1.0
    assert video_creator.transition_clock_shift(0.4) == 3.0, "never less than a frame"
    rng = random.Random(11)
    negative_leads = 0
    for kind in KINDS[1:]:
        for fps in (24, 25, 30):
            creator = VideoCreator(resolution=(640, 360), fps=fps, slide_transition=kind, transition_duration=0.5)
            clips = [SlideClipInfo(slide_index=i, image_path=Path(f"s{i}.png"),
                                   video_path=Path(f"a{i}.mp4") if i % 3 == 2 else None) for i in range(12)]
            durations = [rng.uniform(0.3, 9.0) for _ in clips]
            gaps = [rng.uniform(0.05, 1.5) for _ in clips[:-1]] + [0.0]
            segments = creator.deck_segments(clips, durations, gaps)
            seconds = [s.seconds for s in segments]
            bounds = video_creator.frame_boundaries(seconds, fps)
            starts = [math.fsum(seconds[:k]) for k in range(len(seconds))]
            negative_leads += sum(1 for k, s in enumerate(segments) if s.kind == "slide" and bounds[k] < starts[k] * fps)
            _, graph, _ = video_creator.deck_chunk_graph(segments, bounds, starts, 0, len(segments), fps, (640, 360))
            clocks = _CLOCK.findall(graph)
            assert clocks, (kind, graph[:200])
            assert graph.count("setpts=(N+") == len(clocks), "every transition clock carries the shift"
            for offset, rate, shift in clocks:
                assert float(offset) / float(rate) + float(shift) >= 0, (kind, offset, rate, shift)
            assert "st=0:" not in graph and "clip(t/" not in graph, "nothing is timed from an unshifted zero"
            if kind in video_creator.FADE_COLOURS or kind == "zoom-in":
                assert "fade=t=in:st=1.000000:" in graph
    assert negative_leads > 50, "the decks really had slides whose start rounds down"


# ── the real binary ───────────────────────────────────────────────────────────

W, H, FPS = 640, 360, 24
QUADRANTS = {"tl": (220, 40, 40), "tr": (40, 200, 40), "bl": (40, 40, 220), "br": (230, 220, 40)}


def _quadrant_still(path: Path) -> None:
    """A 320x180 still - four coloured quadrants and a white square in the
    middle - so a frame says which part of the slide is where."""
    image = Image.new("RGB", (320, 180))
    for name, box in (("tl", (0, 0, 160, 90)), ("tr", (160, 0, 320, 90)), ("bl", (0, 90, 160, 180)),
                      ("br", (160, 90, 320, 180))):
        image.paste(QUADRANTS[name], box)
    image.paste((255, 255, 255), (140, 70, 180, 110))
    image.save(path)


def _noise(ffmpeg: str, path: Path, seconds: float, rate: int = 24000, amplitude: float = 0.25) -> None:
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"anoisesrc=color=pink:amplitude={amplitude}:sample_rate={rate}:duration={seconds}:seed=7",
                    "-ac", "1", "-c:a", "pcm_s16le", str(path)], check=True, capture_output=True, timeout=60)


def _spy_master(monkeypatch, keep: Path) -> dict:
    """Record what the master track reported, and keep a copy of it."""
    seen: dict = {}
    real = video_creator._build_master_audio

    def spy(*args, **kwargs):
        path, durations = real(*args, **kwargs)
        seen["durations"], seen["gaps"] = list(durations), list(kwargs.get("gaps_out") or [])
        if path:
            keep.write_bytes(Path(path).read_bytes())
        return path, durations

    monkeypatch.setattr(video_creator, "_build_master_audio", spy)
    return seen


def _deck(ffmpeg: str, tmp_path: Path):
    """Three slides - a still, a 1 s animated clip (shorter than its span, so
    it loops), a still - each with a pink-noise narration in Edge's 24 kHz
    mono."""
    still = tmp_path / "quadrants.png"
    _quadrant_still(still)
    clip = tmp_path / "anim.mp4"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
                   check=True, capture_output=True, timeout=60)
    clips = []
    for i, seconds in enumerate((2.0, 2.2, 2.4)):
        narration = tmp_path / f"narration_{i}.wav"
        _noise(ffmpeg, narration, seconds)
        clips.append(SlideClipInfo(slide_index=i, image_path=still, audio_path=narration,
                                   video_path=clip if i == 1 else None))
    return clips, still


def _creator(kind: str, **kwargs) -> VideoCreator:
    options = dict(
        resolution=(W, H), fps=video_creator.deck_fps(kind, 0.5, animated=True), transition_pause=0.5,
        voice_start_delay=0.25, slide_transition=kind, transition_duration=0.5, intro_text="Welcome",
        intro_subtitle="Q3 review", intro_duration=1.0, outro_text="Thanks", outro_duration=1.0,
        watermark_text="ACME", watermark_position="bottom-right", watermark_opacity=0.5, audio_bitrate="192k",
    )
    options.update(kwargs)
    return VideoCreator(**options)


class _Render:
    """A rendered deck and where everything in it is."""

    def __init__(self, creator, clips, seen, out, ffmpeg):
        self.creator, self.out, self.ffmpeg = creator, out, ffmpeg
        self.segments = creator.deck_segments(clips, seen["durations"], seen["gaps"])
        self.bounds = video_creator.frame_boundaries([s.seconds for s in self.segments], creator.fps)
        self.starts = [math.fsum(s.seconds for s in self.segments[:k]) for k in range(len(self.segments))]
        self.slides = [k for k, s in enumerate(self.segments) if s.kind == "slide"]

    def frame(self, segment: int, seconds: float) -> tuple[int, float]:
        """The frame showing ``seconds`` into a segment, and the segment
        time that frame is actually at."""
        lead = self.bounds[segment] - self.starts[segment] * FPS
        count = self.bounds[segment + 1] - self.bounds[segment]
        j = min(count - 1, max(0, round(seconds * FPS - lead)))
        return self.bounds[segment] + j, (j + lead) / FPS

    def decode(self, indices) -> dict:
        wanted = sorted(set(indices))
        expr = "+".join(f"eq(n,{i})" for i in wanted)
        raw = subprocess.run([self.ffmpeg, "-v", "error", "-i", str(self.out), "-vf", f"select='{expr}'",
                              "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, timeout=120, check=True).stdout
        frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(np.float64)
        assert len(frames) == len(wanted), (len(frames), wanted)
        return dict(zip(wanted, frames))


def _render(ffmpeg, tmp_path, monkeypatch, kind, **kwargs) -> _Render:
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    clips, _ = _deck(ffmpeg, tmp_path)
    seen = _spy_master(monkeypatch, tmp_path / "master.mp3")
    creator = _creator(kind, **kwargs)
    out = tmp_path / f"deck_{kind}.mp4"
    assert creator.create_video(clips, out, slide_titles=["One", "Two", "Three"]) is True
    return _Render(creator, clips, seen, out, ffmpeg)


def _patch(frame, x, y, half=8):
    """The mean colour of a 16x16 patch at (x, y), as fractions of the size."""
    cx, cy = int(x * W), int(y * H)
    return frame[cy - half:cy + half, cx - half:cx + half].reshape(-1, 3).mean(axis=0)


def _near(colour, want, tolerance=16) -> bool:
    return bool(np.abs(np.asarray(colour) - np.asarray(want, dtype=float)).max() <= tolerance)


def _expected_still() -> np.ndarray:
    """The slide as moviepy showed it: the still stretched to the frame."""
    image = Image.new("RGB", (320, 180))
    for name, box in (("tl", (0, 0, 160, 90)), ("tr", (160, 0, 320, 90)), ("bl", (0, 90, 160, 180)),
                      ("br", (160, 90, 320, 180))):
        image.paste(QUADRANTS[name], box)
    image.paste((255, 255, 255), (140, 70, 180, 110))
    return np.asarray(image.resize((W, H), Image.LANCZOS)).astype(np.float64)


def _white_run(frame, threshold) -> int:
    """How wide the white square is along the middle row."""
    row = frame[H // 2]
    return int((row.min(axis=1) > threshold).sum())


def _channel(ffmpeg: str, media: Path, wav: Path, rate: int = 48000) -> np.ndarray:
    """The first channel, decoded at ``rate`` with no down-mix."""
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(media), "-vn",
                    "-ar", str(rate), "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True, timeout=60)
    with wave.open(str(wav), "rb") as wf:
        channels = wf.getnchannels()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float64)
    return samples.reshape(-1, channels)[:, 0]


def _rms_db(samples: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(samples ** 2))))


@needs_ffmpeg
@pytest.mark.parametrize("kind", KINDS)
@ON_EVERY_FFMPEG
def test_a_deck_renders_each_transition_as_its_name_says(tmp_path, monkeypatch, ffmpeg, kind):
    ffprobe = _ffprobe_beside(ffmpeg)
    if ffprobe is None:
        pytest.skip(f"no ffprobe beside {ffmpeg}")
    r = _render(ffmpeg, tmp_path, monkeypatch, kind)
    first, animated, third = r.slides
    fd = r.segments[third].effect_seconds

    # The encode, and the length against the master track.
    info = json.loads(subprocess.run(
        [str(ffprobe), "-v", "error", "-count_frames", "-show_entries",
         "stream=codec_type,codec_name,profile,pix_fmt,r_frame_rate,nb_read_frames,sample_rate,channels",
         "-of", "json", str(r.out)], capture_output=True, text=True, timeout=120, check=True).stdout)
    streams = {s["codec_type"]: s for s in info["streams"] if s["codec_type"] in ("video", "audio")}
    video, audio = streams["video"], streams["audio"]
    assert (video["codec_name"], video["profile"], video["pix_fmt"], video["r_frame_rate"]) == ("h264", "High", "yuv420p", "24/1")
    assert int(video["nb_read_frames"]) == r.bounds[-1]
    exact = math.fsum(s.seconds for s in r.segments)
    assert abs(r.bounds[-1] / FPS - exact) <= 1 / FPS, "within a frame of the master track and the cards"
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("aac", "48000", 2)

    still = _expected_still()
    looks = {
        "mid": r.frame(third, 1.3),
        "in": r.frame(third, fd / 2),
        "out": r.frame(first, r.segments[first].seconds - fd / 2) if fd else r.frame(first, 1.0),
        "card": r.frame(0, 0.5),
        "pause": r.frame(first + 1, r.segments[first + 1].seconds / 2),
        "anim_a": r.frame(animated, 0.8),
        "anim_b": r.frame(animated, 1.8),
        "anim_c": r.frame(animated, 1.0),
    }
    frames = r.decode(index for index, _ in looks.values())
    mid = frames[looks["mid"][0]]

    # Mid-slide is the slide (outside the watermark), quadrant by quadrant.
    for name, (x, y) in {"tl": (0.25, 0.25), "tr": (0.75, 0.25), "bl": (0.25, 0.75), "br": (0.75, 0.6)}.items():
        assert _near(_patch(mid, x, y), QUADRANTS[name]), (name, _patch(mid, x, y))

    # The watermark corner: blended as moviepy blended it - its text drawn by
    # Pillow, its alpha (a / 255) x 0.5 x 255 truncated, flush with the corner,
    # built here rather than by the code under test - and visible.
    from core.fonts import text_image
    drawn = text_image("ACME", 24, "white", font=r.creator.font)
    layer = drawn.convert("RGB").convert("RGBA")
    layer.putalpha(Image.fromarray((0.5 * (np.asarray(drawn.getchannel("A"), dtype=np.float64) / 255) * 255).astype("uint8")))
    wx, wy = W - layer.width, H - layer.height
    assert r.creator._watermark()[1] == (wx, wy)
    expected = Image.fromarray(still.astype(np.uint8)).convert("RGBA")
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.paste(layer, (wx, wy))
    expected = np.asarray(Image.alpha_composite(expected, canvas).convert("RGB")).astype(np.float64)
    box = (slice(wy, H), slice(wx, W))
    assert np.abs(mid[box].mean(axis=(0, 1)) - expected[box].mean(axis=(0, 1))).max() < 6, "the corner's blend"
    assert np.abs(mid[box] - still[box]).max() > 40, "the watermark is there"

    # The card, under the watermark too.
    card = frames[looks["card"][0]]
    assert card[int(H * 0.4): int(H * 0.4) + 40].max() > 200, "the title at 40 %"
    assert card[: int(H * 0.3)].max() < 30, "on black"

    # The animated slide: looped over its span (1 s clip, 2.45 s span) and moving.
    a, b, c = (frames[looks[key][0]] for key in ("anim_a", "anim_b", "anim_c"))
    assert np.abs(a - b).mean() < 4, "the same picture one clip-length later: looped"
    assert np.abs(a - c).mean() > 4, "and it moves"

    opening, at = looks["in"]
    open_frame = frames[opening]
    p = at / fd if fd else 1.0
    pause = frames[looks["pause"][0]]
    if kind == "none":
        assert _near(_patch(open_frame, 0.25, 0.25), QUADRANTS["tl"]), "no transition: the slide from its first frame"
    elif kind in ("fade-to-black", "crossfade", "fade-to-white"):
        white = kind == "fade-to-white"
        assert 0.25 < p < 0.75
        for name, (x, y) in {"tl": (0.25, 0.25), "br": (0.75, 0.6)}.items():
            want = np.asarray(QUADRANTS[name]) * p + (255 * (1 - p) if white else 0)
            assert _near(_patch(open_frame, x, y), want, 20), (kind, name, p, _patch(open_frame, x, y), want)
        closing, at_out = looks["out"]
        q = (r.segments[first].seconds - at_out) / fd
        want = np.asarray(QUADRANTS["tl"]) * q + (255 * (1 - q) if white else 0)
        assert _near(_patch(frames[closing], 0.25, 0.25), want, 20), (kind, q, _patch(frames[closing], 0.25, 0.25))
        assert _near(_patch(pause, 0.5, 0.5), (255, 255, 255) if white else (0, 0, 0), 6), "the pause"
    elif kind.startswith("slide-"):
        assert 0.4 < p < 0.6, p
        black = (0, 0, 0)
        expect = {
            # Enters from the right, moving left: black on the left, the slide's left half on the right.
            "slide-left": {(0.2, 0.25): black, (0.8, 0.25): QUADRANTS["tl"], (0.8, 0.75): QUADRANTS["bl"]},
            # Enters from the left, moving right: the slide's right half on the left, black on the right.
            "slide-right": {(0.2, 0.25): QUADRANTS["tr"], (0.2, 0.75): QUADRANTS["br"], (0.8, 0.25): black},
            # Enters from the bottom, moving up: black on top, the slide's top half below.
            "slide-up": {(0.25, 0.15): black, (0.25, 0.85): QUADRANTS["tl"], (0.75, 0.85): QUADRANTS["tr"]},
            # Enters from the top, moving down: the slide's bottom half on top, black below.
            "slide-down": {(0.25, 0.15): QUADRANTS["bl"], (0.75, 0.15): QUADRANTS["br"], (0.25, 0.85): black},
        }[kind]
        for (x, y), want in expect.items():
            assert _near(_patch(open_frame, x, y), want), (kind, (x, y), _patch(open_frame, x, y), want)
        assert _near(_patch(pause, 0.5, 0.5), (0, 0, 0), 6)
    elif kind == "zoom-in":
        assert 0.3 < p < 0.7
        assert open_frame.mean() < 0.75 * mid.mean(), "darker: fading up from black"
        assert _white_run(open_frame, 255 * p * 0.55) >= 1.08 * _white_run(mid, 140), (
            _white_run(open_frame, 255 * p * 0.55), _white_run(mid, 140))

    # The narration at its SOURCE level: the unity up-mix, never -3 dB.
    voice = _channel(ffmpeg, r.out, tmp_path / "out.wav")
    master = _channel(ffmpeg, tmp_path / "master.mp3", tmp_path / "master.wav")
    # And the picture as long as the track it was given (plus the outro card,
    # which has no narration), read off the track itself: within a frame and
    # a half (the MP3's own decoded length is a little loose).
    picture = r.bounds[-1] / FPS
    assert abs(picture - (len(master) / 48000 + 1.0)) <= 1.5 / FPS, (picture, len(master) / 48000)
    span = slice(int(1.3 * 48000), int((1.3 + 1.6) * 48000))          # inside the first slide's narration
    drop = _rms_db(voice[span]) - _rms_db(master[span])
    assert abs(drop) <= 0.5, f"the narration is {drop:+.2f} dB against the master track"


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_deck_in_several_chunks_has_exactly_the_frames_of_one(tmp_path, monkeypatch, ffmpeg):
    """The same deck in one ffmpeg run and in several (chunks of at most two
    segments, none under 12 frames) joined by a stream copy: the same frame
    count, and the same picture on either side of every join."""
    (tmp_path / "whole").mkdir()
    whole = _render(ffmpeg, tmp_path / "whole", monkeypatch, "crossfade")
    monkeypatch.setattr(video_creator, "MIN_CHUNK_FRAMES", 12)
    commands: list = []
    real_run = video_creator._run_until_done

    def spy(cmd, *args, **kwargs):
        commands.append(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(video_creator, "_run_until_done", spy)
    (tmp_path / "chunked").mkdir()
    real_init = VideoCreator.__init__

    def small_chunks(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self.chunk_limit = 2

    monkeypatch.setattr(VideoCreator, "__init__", small_chunks)
    chunked = _render(ffmpeg, tmp_path / "chunked", monkeypatch, "crossfade")
    encodes = [cmd for cmd in commands if "-c:v" in cmd]
    assert len(encodes) >= 3, f"{len(encodes)} chunk(s)"
    assert chunked.bounds == whole.bounds
    joins = sorted({chunked.bounds[first] for first, _ in video_creator.deck_chunks(chunked.bounds, 2, 12)} - {0})
    assert len(joins) == len(encodes) - 1
    indices = [i for b in joins for i in (b - 1, b) if 0 <= i < whole.bounds[-1]] + [whole.bounds[-1] - 1]
    a, b = whole.decode(indices), chunked.decode(indices)
    for i in indices:
        assert np.abs(a[i] - b[i]).mean() < 3, f"frame {i} differs across the join"


@needs_ffmpeg
@pytest.mark.parametrize("tracks", ["playlist", "single"])
@ON_EVERY_FFMPEG
def test_the_music_plays_in_order_looped_and_faded_under_the_narration(tmp_path, monkeypatch, ffmpeg, tracks):
    """A 150 Hz narration for 6 s under a playlist of 1.5 s at 500 Hz and
    1.5 s at 900 Hz (or the 500 Hz track alone): the tracks in order, the
    playlist again from its first track when it runs out, faded in over the
    first second and out over the last, at the music volume - and the
    narration at its own level under it."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    rate = 48000

    def tone(path, hertz, seconds, channels):
        subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency={hertz}:sample_rate={rate}:duration={seconds}", "-af", "volume=0.5",
                        "-ac", str(channels), "-c:a", "pcm_s16le", str(path)], check=True, capture_output=True, timeout=60)

    narration, one, two = tmp_path / "voice.wav", tmp_path / "one.wav", tmp_path / "two.wav"
    tone(narration, 150, 6.0, 1)
    tone(one, 500, 1.5, 2)
    tone(two, 900, 1.5, 2)
    playlist = [one, two] if tracks == "playlist" else [one]
    seen = _spy_master(monkeypatch, tmp_path / "master.mp3")
    still = tmp_path / "still.png"
    _quadrant_still(still)
    creator = VideoCreator(resolution=(320, 180), fps=2, transition_pause=0.0, voice_start_delay=0.0,
                           background_music_paths=playlist, music_volume=0.5, music_fade_duration=1.0,
                           audio_bitrate="192k")
    out = tmp_path / "deck.mp4"
    assert creator.create_video([SlideClipInfo(0, image_path=still, audio_path=narration)], out) is True
    assert seen["durations"]
    sound = _channel(ffmpeg, out, tmp_path / "out.wav")

    def level(samples, start, end, hertz):
        """A tone's amplitude in ``samples`` over [start, end) (a Hann-windowed bin)."""
        part = samples[int(start * rate):int(end * rate)]
        spectrum = np.abs(np.fft.rfft(part * np.hanning(len(part))))
        freqs = np.fft.rfftfreq(len(part), 1 / rate)
        return float(spectrum[np.abs(freqs - hertz) < 15].max() / (len(part) / 4))

    full = level(sound, 1.1, 1.4, 500)                # after the fade in, before the track ends
    assert level(sound, 0.05, 0.3, 500) < 0.4 * full, "faded in"
    second = (900, 2.0, 2.5) if tracks == "playlist" else (500, 2.0, 2.5)
    assert level(sound, second[1], second[2], second[0]) > 0.7 * full, "the next track in order"
    if tracks == "playlist":
        assert level(sound, 2.0, 2.5, 500) < 0.1 * full, "not the first track"
    assert level(sound, 3.4, 3.9, 500) > 0.7 * full, "the playlist again from its first track: looped"
    last = 900 if tracks == "playlist" else 500
    assert level(sound, 5.6, 5.9, last) < 0.5 * level(sound, 4.6, 4.9, last), "faded out"
    # The music at its volume - 0.5 x the track's own level, a linear factor -
    # and the narration at its own level under it: both read off the files.
    track = _channel(ffmpeg, one, tmp_path / "one_48k.wav")
    voice = _channel(ffmpeg, narration, tmp_path / "voice_48k.wav")
    assert full == pytest.approx(0.5 * level(track, 0.2, 0.5, 500), rel=0.06), "the music volume, linear"
    assert level(sound, 1.1, 1.4, 150) == pytest.approx(level(voice, 1.1, 1.4, 150), rel=0.06), (
        "the narration at its source level (0.5 dB is 6 %)")


# ── a fade-in on either side of the frame grid ────────────────────────────────

FADING_KINDS = ["fade-to-black", "crossfade", "fade-to-white", "zoom-in"]


def _exact_narrations(monkeypatch) -> None:
    """Keep each narration's exact length (the trim is not under test), so a
    test knows where every slide starts to a thousandth of a second."""
    monkeypatch.setattr(video_creator, "_trim_leading_silence", lambda path, profile=None: path)


@needs_ffmpeg
@pytest.mark.parametrize("pause", [0.51, 0.53], ids=["start-rounds-down", "start-rounds-up"])
@pytest.mark.parametrize("kind", FADING_KINDS)
@ON_EVERY_FFMPEG
def test_a_fade_in_fades_whichever_way_the_slides_start_rounds(tmp_path, monkeypatch, ffmpeg, kind, pause):
    """Three slides of 2.0 s - a grey still, a grey still, an animated clip -
    with a 0.51 s pause after the first (slides 2 and 3 start 0.24 of a frame
    AFTER the frame they were rounded to: a NEGATIVE first timestamp) or a
    0.53 s one (0.28 of a frame before it: positive). Either way, for every
    kind that fades and for the still and the animated slide alike, the
    slide's first frame is dark (white for fade-to-white), half way through it
    is half way, and after the fade it is itself.

    ffmpeg's ``fade`` never fades from a negative first timestamp: before the
    transition clock was shifted, every slide whose start rounded down cut in
    at full brightness (8 of the 19 on the owner's deck)."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    _exact_narrations(monkeypatch)
    still = tmp_path / "grey.png"
    Image.new("RGB", (320, 180), (100, 100, 100)).save(still)
    clip = tmp_path / "anim.mp4"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
                   check=True, capture_output=True, timeout=60)
    clips = []
    for i in range(3):
        narration = tmp_path / f"narration_{i}.wav"
        _noise(ffmpeg, narration, 2.0)
        clips.append(SlideClipInfo(slide_index=i, image_path=still, audio_path=narration,
                                   video_path=clip if i == 2 else None, pause_after=pause if i == 0 else 0.5))
    seen = _spy_master(monkeypatch, tmp_path / "master.mp3")
    creator = VideoCreator(resolution=(W, H), fps=FPS, transition_pause=0.5, voice_start_delay=0.0,
                           slide_transition=kind, transition_duration=0.5, audio_bitrate="192k")
    out = tmp_path / "deck.mp4"
    assert creator.create_video(clips, out) is True
    r = _Render(creator, clips, seen, out, ffmpeg)
    assert seen["durations"] == [2.0, 2.0, 2.0], "the narrations kept their length"

    slides = {"still": r.slides[1], "animated": r.slides[2]}
    leads = {name: r.bounds[k] - r.starts[k] * FPS for name, k in slides.items()}
    for name, lead in leads.items():
        if pause == 0.51:
            assert -0.3 < lead < -0.2, f"the {name} slide's first frame is BEFORE its start: {lead:+.3f}"
        else:
            assert 0.2 < lead < 0.4, f"the {name} slide's first frame is after its start: {lead:+.3f}"

    frames = r.decode([r.bounds[k] + j for k in slides.values() for j in (0, 6, 20)])
    through = 255.0 if kind == "fade-to-white" else 0.0
    for name, k in slides.items():
        first, middle, settled = (float(frames[r.bounds[k] + j][40:320, 40:600].mean()) for j in (0, 6, 20))
        assert abs(settled - through) > 60, (name, settled)
        # How far from the fade's colour to the slide itself: 0 at the colour, 1 on the slide.
        at_first = (first - through) / (settled - through)
        at_middle = (middle - through) / (settled - through)
        expected = (6 + leads[name]) / 12                      # 12 frames of fade
        slack = 0.08 if name == "still" else 0.2               # the zoom crops a moving clip
        assert at_first < 0.12, f"{kind}, {name}, lead {leads[name]:+.2f}: the first frame is {at_first:.2f} of the way in"
        assert abs(at_middle - expected) < slack, (
            f"{kind}, {name}, lead {leads[name]:+.2f}: half way it is {at_middle:.2f}, not {expected:.2f}")


# ── the watermark on its own pixel ────────────────────────────────────────────

def test_even_origin_pads_an_odd_corner_and_leaves_an_even_one():
    """``overlay`` snaps an odd x or y down to even in 4:2:0. The layer gains
    one clear column or row on each odd side and is placed one pixel earlier,
    so its pixels land where they were asked to."""
    layer = Image.new("RGBA", (5, 3), (255, 255, 255, 200))
    assert video_creator.even_origin(layer, 10, 20) == (layer, 10, 20)
    padded, x, y = video_creator.even_origin(layer, 7, 9)
    assert (padded.size, x, y) == ((6, 4), 6, 8)
    pixels = np.asarray(padded)
    assert pixels[0, :, 3].max() == 0 and pixels[:, 0, 3].max() == 0, "the added row and column are clear"
    assert (pixels[1:, 1:] == np.asarray(layer)).all(), "the layer itself is untouched, one pixel in"
    padded, x, y = video_creator.even_origin(layer, 7, 20)
    assert (padded.size, x, y) == ((6, 3), 6, 20)
    padded, x, y = video_creator.even_origin(layer, 10, 9)
    assert (padded.size, x, y) == ((5, 4), 10, 8)


@needs_ffmpeg
@pytest.mark.parametrize("position", ["center", "bottom-right"])
@ON_EVERY_FFMPEG
def test_the_watermark_is_drawn_on_exactly_the_pixel_it_was_placed_at(tmp_path, monkeypatch, ffmpeg, position):
    """A half-opaque white mark over a DARK slide, at an ODD pixel (both
    coordinates for the centre; the row for the bottom-right corner, the
    owner's case: "Pentaho" belongs at y 1057 and was drawn a row higher).
    The rendered frame is compared pixel by pixel with a reference composite
    built here: it fits the reference where the mark was placed, and fits it
    far worse one pixel up, left, down or right."""
    from core.fonts import text_image

    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    font = VideoCreator(resolution=(64, 64)).font
    drawn = text_image("ACME", 24, "white", font=font)
    alpha = (0.5 * (np.asarray(drawn.getchannel("A"), dtype=np.float64) / 255) * 255).astype("uint8")
    layer = drawn.convert("RGB").convert("RGBA")
    layer.putalpha(Image.fromarray(alpha))
    lw, lh = layer.size
    if position == "center":
        # int((width - lw) / 2) = 141 and int((height - lh) / 2) = 85, whatever the font's size.
        width, height = lw + 2 * 141 + lw % 2, lh + 2 * 85 + lh % 2
        x, y = 141, 85
    else:
        if lh % 2 == 0:
            pytest.skip(f"this host's font draws the mark {lh} px high: its bottom-right row is even")
        width, height = 640, 360
        x, y = width - lw, height - lh
    assert x % 2 or y % 2, (x, y)

    still = tmp_path / "dark.png"
    Image.new("RGB", (width, height), (20, 20, 20)).save(still)
    narration = tmp_path / "voice.wav"
    _noise(ffmpeg, narration, 1.0)
    creator = VideoCreator(resolution=(width, height), fps=2, transition_pause=0.0, voice_start_delay=0.0,
                           watermark_text="ACME", watermark_position=position, watermark_opacity=0.5,
                           video_bitrate="20M")
    assert creator._watermark()[1] == (x, y), "where the mark was placed"
    out = tmp_path / "deck.mp4"
    assert creator.create_video([SlideClipInfo(0, image_path=still, audio_path=narration)], out) is True
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=120, check=True).stdout
    got = np.frombuffer(raw, np.uint8).reshape(height, width, 3).astype(np.float64).mean(axis=2)

    def reference(at_x, at_y) -> np.ndarray:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(layer, (at_x, at_y))
        dark = Image.new("RGBA", (width, height), (20, 20, 20, 255))
        return np.asarray(Image.alpha_composite(dark, canvas).convert("RGB")).astype(np.float64).mean(axis=2)

    box = (slice(max(0, y - 3), min(height, y + lh + 3)), slice(max(0, x - 3), min(width, x + lw + 3)))

    def misfit(at_x, at_y) -> float:
        return float(np.abs(got[box] - reference(at_x, at_y)[box]).mean())

    placed = misfit(x, y)
    moved = {"up": misfit(x, y - 1), "left": misfit(x - 1, y), "down": misfit(x, y + 1), "right": misfit(x + 1, y)}
    assert got[box].max() > 100, "the mark is there"
    assert placed < 3.0, f"the frame is {placed:.2f} grey levels a pixel from the reference at ({x}, {y})"
    assert placed < 0.5 * min(moved.values()), f"at ({x}, {y}): {placed:.2f}; one pixel off: {moved}"


# ── the end of the track ──────────────────────────────────────────────────────

@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_the_picture_never_ends_before_the_narration_does(tmp_path, monkeypatch, ffmpeg):
    """One slide with 2.2 s of narration at 2 fps and no outro card: the
    picture is 5 frames (2.5 s), not the nearest 4 (2.0 s), and the last
    0.2 s of the narration is in the file at its own level - not trimmed to
    the last frame."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    _exact_narrations(monkeypatch)
    ffprobe = _ffprobe_beside(ffmpeg)
    if ffprobe is None:
        pytest.skip(f"no ffprobe beside {ffmpeg}")
    narration = tmp_path / "voice.wav"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=24000:duration=2.2", "-af", "volume=0.5", "-ac", "1",
                    "-c:a", "pcm_s16le", str(narration)], check=True, capture_output=True, timeout=60)
    still = tmp_path / "still.png"
    _quadrant_still(still)
    seen = _spy_master(monkeypatch, tmp_path / "master.mp3")
    creator = VideoCreator(resolution=(320, 180), fps=2, transition_pause=0.0, voice_start_delay=0.0,
                           audio_bitrate="192k")
    out = tmp_path / "deck.mp4"
    assert creator.create_video([SlideClipInfo(0, image_path=still, audio_path=narration)], out) is True
    assert seen["durations"] == [2.2]
    info = json.loads(subprocess.run(
        [str(ffprobe), "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
         "stream=nb_read_frames", "-of", "json", str(out)], capture_output=True, text=True, timeout=120, check=True).stdout)
    assert int(info["streams"][0]["nb_read_frames"]) == 5, "2.2 s of track is 5 frames at 2 fps, never 4"
    sound = _channel(ffmpeg, out, tmp_path / "out.wav")
    master = _channel(ffmpeg, tmp_path / "master.mp3", tmp_path / "master.wav")
    assert len(sound) / 48000 >= 2.2, f"the sound is {len(sound) / 48000:.3f} s"
    tail = slice(int(2.02 * 48000), int(2.17 * 48000))            # past the 4th frame's end
    assert _rms_db(master[tail]) > 40, "the narration is still speaking there (dB above one 16-bit step)"
    drop = _rms_db(sound[tail]) - _rms_db(master[tail])
    assert abs(drop) <= 0.5, f"the narration's last 0.2 s is {drop:+.2f} dB against the track"


# ── progress across chunks, and what a failure leaves ─────────────────────────

def _small_chunks(monkeypatch, limit=2, min_frames=12) -> None:
    """Every VideoCreator renders in chunks of at most ``limit`` segments."""
    monkeypatch.setattr(video_creator, "MIN_CHUNK_FRAMES", min_frames)
    real_init = VideoCreator.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self.chunk_limit = limit

    monkeypatch.setattr(VideoCreator, "__init__", init)


def _spy_ffmpeg(monkeypatch) -> list:
    """Every process the render starts, as it was started."""
    started: list = []
    real_popen = subprocess.Popen

    class _Spy(real_popen):
        def __init__(self, cmd, *args, **kwargs):
            super().__init__(cmd, *args, **kwargs)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", _Spy)
    return started


def _left_behind(out: Path, tmp_path: Path) -> list:
    """What a render that did not finish must not leave: the partial video and
    its scratch folder."""
    left = [str(p) for p in (tmp_path / "temp").glob("deck-*")]
    if out.with_suffix(".part.mp4").exists():
        left.append(out.with_suffix(".part.mp4").name)
    return left


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_progress_counts_frames_across_the_chunks_of_a_render(tmp_path, monkeypatch, ffmpeg):
    """A deck rendered in several chunks reports frames done OF THE RENDER:
    each chunk's count is added to the frames of the chunks before it, so the
    reports never go back, pass through the frame each chunk ends on, and end
    on the render's total."""
    _small_chunks(monkeypatch)
    progress: list = []
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    clips, _ = _deck(ffmpeg, tmp_path)
    seen = _spy_master(monkeypatch, tmp_path / "master.mp3")
    creator = _creator("crossfade")
    out = tmp_path / "deck.mp4"
    assert creator.create_video(clips, out, encoding_callback=lambda frame, total: progress.append((frame, total))) is True
    r = _Render(creator, clips, seen, out, ffmpeg)
    total = r.bounds[-1]
    chunks = video_creator.deck_chunks(r.bounds, 2)
    assert len(chunks) >= 3
    frames = [frame for frame, _ in progress]
    assert {t for _, t in progress} == {total}, "every report is out of the render's total"
    assert frames == sorted(frames), f"the count went back: {frames}"
    assert frames[-1] == total
    ends = [r.bounds[last] for _, last in chunks]
    assert set(ends) <= set(frames), f"each chunk's end is reported as frames of the render: {ends} in {frames}"
    assert max(r.bounds[last] - r.bounds[first] for first, last in chunks) < total, "no chunk is the whole render"


@needs_ffmpeg
@pytest.mark.parametrize("how", ["fails", "cancelled"])
@ON_EVERY_FFMPEG
def test_a_join_that_fails_or_is_cancelled_leaves_no_partial_video(tmp_path, monkeypatch, ffmpeg, how):
    """The join writes ``<stem>.part.mp4``. When it fails after the file is on
    disk, or is cancelled while it is writing it, the render answers False,
    the partial file and the scratch are gone, the last good render is
    untouched and no ffmpeg is left running."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    clips, _ = _deck(ffmpeg, tmp_path)
    creator = _creator("crossfade")
    out = tmp_path / "deck.mp4"
    out.write_bytes(b"the previous render")
    part = out.with_suffix(".part.mp4")
    started = _spy_ffmpeg(monkeypatch)
    real_run = video_creator._run_until_done
    state = {"join_seen": False, "part_seen": False}

    def run(cmd, log, timeout, cancelled, **kwargs):
        if "chunks.ffconcat" not in cmd:
            return real_run(cmd, log, timeout, cancelled, **kwargs)
        state["join_seen"] = True
        if how == "fails":
            # The join runs to its end - the partial file is written - and is
            # then reported as failed, as an ffmpeg that died at the last would be.
            outcome, _ = real_run(cmd, log, timeout, cancelled, **kwargs)
            state["part_seen"] = part.is_file() and part.stat().st_size > 0
            return outcome, types.SimpleNamespace(returncode=1)
        # Slowed to real time (-re), so the cancel lands while it is writing.
        at = cmd.index("-f")
        return real_run(cmd[:at] + ["-re"] + cmd[at:], log, timeout, cancelled, **kwargs)

    monkeypatch.setattr(video_creator, "_run_until_done", run)

    def cancel_check() -> bool:
        if how != "cancelled" or not state["join_seen"]:
            return False
        if part.is_file() and part.stat().st_size > 0:
            state["part_seen"] = True
        return state["part_seen"]

    assert creator.create_video(clips, out, cancel_check=cancel_check) is False
    assert state["join_seen"] and state["part_seen"], "the partial file was on disk when it went wrong"
    assert _left_behind(out, tmp_path) == []
    assert out.read_bytes() == b"the previous render", "the last good render is untouched"
    assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"
    if how == "cancelled":
        (join,) = [proc for proc in started if "chunks.ffconcat" in proc.args]
        assert join.returncode != 0, "the join was stopped, not left to finish"


class _Errors(logging.Handler):
    """The engine loggers do not propagate (utils/logger.py): collect their
    error records directly."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.messages: list = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_sound_encode_that_fails_is_reported_as_such(tmp_path, monkeypatch, ffmpeg):
    """The sound is encoded by its own ffmpeg beside the picture's. When it
    fails, the render fails AS A SOUND FAILURE - said in the log with ffmpeg's
    own words - instead of joining the picture to whatever is there; nothing
    is left behind and the last good render is untouched."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    clips, _ = _deck(ffmpeg, tmp_path)
    real_graph = video_creator.deck_audio_graph
    monkeypatch.setattr(video_creator, "deck_audio_graph",
                        lambda *args, **kwargs: real_graph(*args, **kwargs).replace("aresample", "nosuchfilter", 1))
    started = _spy_ffmpeg(monkeypatch)
    errors = _Errors()
    engine_log = logging.getLogger("mediastudio.VIDEO")
    engine_log.addHandler(errors)
    try:
        out = tmp_path / "deck.mp4"
        out.write_bytes(b"the previous render")
        assert _creator("none").create_video(clips, out) is False
    finally:
        engine_log.removeHandler(errors)
    assert any("The sound could not be encoded" in message and "nosuchfilter" in message
               for message in errors.messages), errors.messages
    assert not any("chunks.ffconcat" in proc.args for proc in started), "the join never ran on a missing sound"
    assert _left_behind(out, tmp_path) == []
    assert out.read_bytes() == b"the previous render"
    assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_cancel_stops_the_sound_encode_as_well_as_the_picture(tmp_path, monkeypatch, ffmpeg):
    """The sound is encoded by its own ffmpeg beside the picture's, so a
    cancel has two processes to stop. The sound is slowed to real time here
    (``-re``) so that it is certainly still running when the cancel lands on
    the picture's encode: both are stopped, neither is left to finish, and
    nothing is left behind. (With a sound encode that is over in a second,
    whether a cancel finds it running is a matter of luck.)"""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    clips, _ = _deck(ffmpeg, tmp_path)
    real_inputs = VideoCreator._sound_inputs

    def slow_sound(self, master, seconds, first_input):
        inputs, graph = real_inputs(self, master, seconds, first_input)
        return ["-re", *inputs], graph

    monkeypatch.setattr(VideoCreator, "_sound_inputs", slow_sound)
    started = _spy_ffmpeg(monkeypatch)
    flag = {"cancelled": False}

    def running(marker) -> bool:
        return any(marker in proc.args and proc.poll() is None for proc in started)

    def cancel_check() -> bool:
        if running("-c:v") and running("sound.m4a"):
            flag["cancelled"] = True
        return flag["cancelled"]

    out = tmp_path / "deck.mp4"
    out.write_bytes(b"the previous render")
    try:
        assert _creator("crossfade").create_video(clips, out, cancel_check=cancel_check) is False
        assert flag["cancelled"], "the cancel came while the picture and the sound were both encoding"
        (sound,) = [proc for proc in started if "sound.m4a" in proc.args]
        (picture,) = [proc for proc in started if "-c:v" in proc.args]
        assert sound.poll() is not None, "the sound encode was left running"
        assert sound.returncode != 0 and picture.returncode != 0, "both were stopped, neither left to finish"
        assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"
        assert _left_behind(out, tmp_path) == []
        assert out.read_bytes() == b"the previous render"
    finally:
        for proc in started:                       # never leave one behind, whatever the test found
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_cancel_mid_encode_stops_ffmpeg_and_leaves_nothing(tmp_path, monkeypatch, ffmpeg):
    """A 1080p deck at 24 fps long enough to be mid-encode: once progress has
    counted frames upward, Cancel. ffmpeg is stopped within a poll or two,
    nothing is left - no video, no partial file, no scratch - and the frames
    reported before the cancel never went back (a leading 0 is allowed)."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    narration = tmp_path / "voice.wav"
    _noise(ffmpeg, narration, 60.0)
    still = tmp_path / "still.png"
    _quadrant_still(still)
    started: list = []
    real_popen = subprocess.Popen

    class _Spy(real_popen):
        def __init__(self, cmd, *args, **kwargs):
            super().__init__(cmd, *args, **kwargs)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", _Spy)
    progress: list = []
    flag = {"at": None}

    def on_frame(frame, total):
        progress.append((frame, total))
        if flag["at"] is None and len({f for f, _ in progress}) >= 2 and frame > 0:
            flag["at"] = time.monotonic()

    creator = VideoCreator(resolution=(1920, 1080), fps=24, transition_pause=0.0, voice_start_delay=0.0)
    out = tmp_path / "deck.mp4"
    out.write_bytes(b"the previous render")
    ok = creator.create_video([SlideClipInfo(0, image_path=still, audio_path=narration)], out,
                              encoding_callback=on_frame, cancel_check=lambda: flag["at"] is not None)
    stopped = time.monotonic()
    assert ok is False and flag["at"] is not None, "the cancel came mid-encode"
    (encode,) = [proc for proc in started if "-c:v" in proc.args]
    assert encode.returncode != 0, "the encode was stopped, not left to finish"
    assert progress[-1][0] < progress[-1][1], f"stopped part way: {progress[-1]}"
    assert stopped - flag["at"] < 2.0, f"stopped {stopped - flag['at']:.2f}s after the cancel"
    assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"
    assert out.read_bytes() == b"the previous render", "the last good render is untouched"
    assert not out.with_suffix(".part.mp4").exists()
    assert not list((tmp_path / "temp").glob("deck-*")), "the scratch is gone"
    # Counting upward: never back, and past zero by the time of the cancel. The
    # FIRST report may be 0 - ffmpeg writes a progress block before x264 has
    # given it a frame ([0, 211] is a correct history).
    frames = [f for f, _ in progress]
    assert frames == sorted(frames) and frames[-1] > 0 and len(set(frames)) >= 2, frames
    assert len({total for _, total in progress}) == 1 and progress[0][1] > 60 * 24 - 24, "out of the render's own total"
