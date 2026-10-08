"""The WebM export's encode, and how long the extra formats may take (#p1-webm).

``generate_extra_formats`` wrote the WebM with ``libvpx-vp9 -crf 30 -b:v 0``
and libvpx's defaults - the "good" deadline at cpu-used 1, no row threading
- under a flat 600 s timeout, whatever the video's length. On the owner's
20-slide deck the WebM of a 7.4-minute 1080p video took about 9 minutes
(0.13.0, measured on the bundled 7.1), a minute short of being killed, and
634.8 s on a loaded machine - past it. Measured on that video with the
ffmpeg the app ships, the realtime deadline at cpu-used 8 with ``-row-mt 1``
encodes it in 29.0 s at a mean SSIM 0.0005 under the old settings' (the
rule: the fastest setting whose SSIM is within 0.01 of today's; see the
0.14.1 changelog entry for every candidate's time, size and SSIM).

What these hold in place: the WebM's argv carries the chosen options, and
the WebM's and the GIF's timeouts follow the source's length - 60 s plus
twice the length, never under 300 s, and the old 600 s only when the length
cannot be told - while the MP3's stays what it was. The runs are faked
(``_run_until_done``), so no ffmpeg runs here; the encode itself is
exercised by ``test_generate_cancel`` on every real ffmpeg.
"""

from pathlib import Path

import pytest

from core import video_creator
from services import processing
from utils import config as config_module

# What the WebM is encoded with, in the order the argv carries it.
CHOSEN = ["-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
          "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1"]


def _contains(argv, sequence) -> bool:
    n = len(sequence)
    return any(argv[i:i + n] == sequence for i in range(len(argv) - n + 1))


@pytest.fixture
def runs(monkeypatch, tmp_path):
    """Every ffmpeg run the formats would start, as (argv, timeout); none
    runs, and each "fails" (no part file), so the loop goes on to the next."""
    started = []

    class _Exited:
        returncode = 1

    def run(cmd, log, timeout, cancelled, **kwargs):
        started.append((list(cmd), timeout))
        return "finished", _Exited()

    monkeypatch.setattr(video_creator, "_run_until_done", run)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-for-this-test")
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    return started


def _formats(tmp_path, monkeypatch, duration, **kinds):
    monkeypatch.setattr(video_creator, "_probe_duration", lambda path: duration)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    return processing.generate_extra_formats(video, **kinds)


def _run_of(runs, kind_suffix):
    (match,) = [(argv, timeout) for argv, timeout in runs if argv[-1].endswith(kind_suffix)]
    return match


# -- the encode ----------------------------------------------------------------

def test_the_webm_is_encoded_with_the_chosen_vp9_settings(runs, tmp_path, monkeypatch):
    _formats(tmp_path, monkeypatch, 443.0, webm=True)
    argv, _timeout = _run_of(runs, ".part.webm")
    assert _contains(argv, CHOSEN), argv
    assert _contains(argv, ["-c:a", "libopus"]), argv
    # the options are the module's one list, so the guide and this test
    # describe what the app does
    assert processing.WEBM_VP9_OPTIONS == CHOSEN


def test_the_gif_and_the_mp3_keep_their_own_options(runs, tmp_path, monkeypatch):
    _formats(tmp_path, monkeypatch, 443.0, gif=True, audio_only=True)
    gif, _ = _run_of(runs, ".part.gif")
    mp3, _ = _run_of(runs, ".part.mp3")
    assert _contains(gif, ["-t", "30", "-vf", "fps=5,scale=480:-1:flags=lanczos"]), gif
    assert _contains(mp3, ["-vn", "-acodec", "libmp3lame", "-q:a", "2"]), mp3
    assert "libvpx-vp9" not in gif and "libvpx-vp9" not in mp3


# -- the timeouts --------------------------------------------------------------

@pytest.mark.parametrize("duration, expected", [
    (443.0, 60 + 2 * 443.0),   # the owner's deck video: 946 s, where 600 was a minute from killing it
    (7200.0, 60 + 2 * 7200.0),  # two hours: 14,460 s
    (120.0, 300.0),             # short: the floor
    (30.0, 300.0),
    (None, 600.0),              # unknown: the old flat value
    (0.0, 600.0),
])
def test_an_extra_formats_timeout_follows_the_sources_length(duration, expected):
    assert processing.extra_format_timeout(duration) == expected


def test_the_webm_and_the_gif_are_given_the_length_scaled_timeout(runs, tmp_path, monkeypatch):
    _formats(tmp_path, monkeypatch, 443.0, webm=True, gif=True, audio_only=True)
    _, webm_timeout = _run_of(runs, ".part.webm")
    _, gif_timeout = _run_of(runs, ".part.gif")
    _, mp3_timeout = _run_of(runs, ".part.mp3")
    assert webm_timeout == 946.0, "the WebM's timeout is 60 s plus twice the 443 s source"
    assert gif_timeout == 946.0, "the GIF's follows the same rule"
    assert mp3_timeout == 300.0, "the MP3's is what it was"


def test_a_short_video_gets_the_floor_and_an_unknown_length_the_old_flat_value(runs, tmp_path, monkeypatch):
    _formats(tmp_path, monkeypatch, 45.0, webm=True)
    _formats(tmp_path, monkeypatch, None, webm=True)
    (_, short), (_, unknown) = [(argv, timeout) for argv, timeout in runs]
    assert short == 300.0
    assert unknown == 600.0


def test_the_length_is_read_from_ffmpegs_own_header_only_when_a_webm_or_gif_is_asked_for(runs, tmp_path, monkeypatch):
    """The source's length comes from ``_probe_duration`` (ffmpeg's header),
    never a prober, so the timeout is right on a machine with no ffprobe -
    and it is asked once, only when a format that needs it is wanted: an MP3
    alone, or nothing, starts no ffmpeg for it."""
    asked = []

    def probe(path):
        asked.append(Path(path).name)
        return 443.0

    monkeypatch.setattr(video_creator, "_probe_duration", probe)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    processing.generate_extra_formats(video)
    processing.generate_extra_formats(video, audio_only=True)
    assert asked == [], "nothing to time: no probe"
    processing.generate_extra_formats(video, webm=True, gif=True, audio_only=True)
    assert asked == ["deck.mp4"], "once, for the WebM and the GIF together"
