"""An audio file with cover art is not a video (#p1-cover).

An ``.mp4`` or ``.m4a``-style file whose only video stream is an attached
picture (``disposition.attached_pic``: a podcast or a tone with a jpg as its
cover) imported as a video project, and a re-voice with a cut ran
``cut_picture`` on it: ffmpeg exits 0 and writes a one-frame video stream of
no length (``Duration: N/A``, measured on 8.0.1 and the bundled 7.1), the cut
reported success, and the re-voice went on to build a silent file with no
picture (the Q1 review's finding).

Two guards now, both on every real ffmpeg on the machine, with a fixture
ffmpeg itself makes (a tone and a jpg as ``attached_pic`` in an mp4):

- the import refuses such a file with a plain message, probing with the
  ffprobe that ships beside the ffmpeg under test (no video stream, or every
  video stream an attached picture); a real video still imports, and a file
  ffprobe cannot read at all is not refused here (the old behaviour: the
  suite's fake uploads, and the step that needs the picture says so);
- ``cut_picture``'s success requires a moving picture in its output - a
  video stream WITH a length - so the path can never silently write a
  silent file, whatever reaches it.
"""

import subprocess
from pathlib import Path

import pytest

from core import video_creator
from services import projects as store
from utils import config as config_module

from test_generate import client  # noqa: F401 - the logged-in client, used by name
from test_generation_options import REAL_FFMPEGS, needs_ffmpeg

ON_EVERY_FFMPEG = pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")


def _ffprobe_beside(ffmpeg: str) -> str:
    """The ffprobe shipped beside ``ffmpeg`` (the bundled pair, the dev box's
    pair), or the test is skipped: the import's probe is ffprobe's."""
    exe = Path(ffmpeg)
    probe = exe.with_name("ffprobe" + exe.suffix)
    if not probe.is_file():
        pytest.skip(f"no ffprobe beside {ffmpeg}")
    return str(probe)


def _run(ffmpeg: str, *args) -> None:
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args],
                   check=True, capture_output=True, timeout=120)


def _cover_art_mp4(ffmpeg: str, path: Path) -> Path:
    """Three seconds of a tone with a 64x64 jpg as its cover: one audio
    stream and one video stream whose disposition is ``attached_pic``."""
    jpg = path.with_suffix(".jpg")
    _run(ffmpeg, "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1", "-frames:v", "1", str(jpg))
    _run(ffmpeg, "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-i", str(jpg),
         "-map", "0:a", "-map", "1:v", "-c:a", "aac", "-c:v", "mjpeg", "-disposition:v", "attached_pic", str(path))
    return path


def _real_mp4(ffmpeg: str, path: Path) -> Path:
    """Three seconds of a moving test picture with a tone."""
    _run(ffmpeg, "-f", "lavfi", "-i", "testsrc=size=160x90:rate=12:duration=3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path))
    return path


def _upload(client, path: Path):
    return client.post("/api/projects/import", files={"file": (path.name, path.read_bytes(), "video/mp4")})


# -- the import ----------------------------------------------------------------

@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_an_audio_file_with_cover_art_is_refused_at_import_and_a_video_is_not(client, monkeypatch, tmp_path, ffmpeg):  # noqa: F811
    monkeypatch.setattr(config_module, "_FFPROBE_PATH", _ffprobe_beside(ffmpeg))
    cover = _cover_art_mp4(ffmpeg, tmp_path / "podcast.mp4")
    real = _real_mp4(ffmpeg, tmp_path / "clip.mp4")

    refused = _upload(client, cover)
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"] == store.NO_MOVING_PICTURE
    assert refused.json()["detail"] == "This file has no moving picture — an audio file with cover art. Import a video."
    assert client.get("/api/projects").json()["projects"] == [], "nothing of it is kept"
    assert not any(store.PROJECTS_DIR.glob("*/podcast.mp4")), "the refused file is not left on disk"

    accepted = _upload(client, real)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["kind"] == "video" and accepted.json()["source_filename"] == "clip.mp4"


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_the_store_tells_cover_art_from_a_moving_picture(monkeypatch, tmp_path, ffmpeg):
    monkeypatch.setattr(config_module, "_FFPROBE_PATH", _ffprobe_beside(ffmpeg))
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    cover = _cover_art_mp4(ffmpeg, tmp_path / "podcast.mp4")
    real = _real_mp4(ffmpeg, tmp_path / "clip.mp4")
    assert store.has_no_moving_picture(cover) is True
    assert store.has_no_moving_picture(real) is False

    with pytest.raises(ValueError, match="no moving picture"):
        store.import_upload("podcast.mp4", cover.read_bytes())
    assert not (tmp_path / "projects").exists() or not any((tmp_path / "projects").iterdir())
    assert store.import_upload("clip.mp4", real.read_bytes())["kind"] == "video"


def test_a_file_ffprobe_cannot_read_is_not_refused_here(monkeypatch, tmp_path):
    """The old behaviour for anything that is not readable media (the suite's
    fake uploads among them): the probe says nothing, the import goes on, and
    the step that needs the picture is the one that says so."""
    junk = tmp_path / "clip.mp4"
    junk.write_bytes(b"video-bytes")
    if config_module._FFPROBE_PATH:
        assert store.has_no_moving_picture(junk) is False
    monkeypatch.setattr(config_module, "_FFPROBE_PATH", None)
    assert store.has_no_moving_picture(junk) is False, "no ffprobe: nothing is refused"


# -- the cut -------------------------------------------------------------------

@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_cut_picture_refuses_a_source_with_no_moving_picture(monkeypatch, tmp_path, ffmpeg, caplog):
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    cover = _cover_art_mp4(ffmpeg, tmp_path / "podcast.mp4")
    out = tmp_path / "cut.mp4"

    with caplog.at_level("ERROR", logger="mediastudio.VIDEO"):
        assert video_creator.cut_picture(cover, [(0.0, 1.0)], out) is False
    assert "carries no moving picture" in caplog.text, caplog.text
    assert not out.exists(), "nothing is published"
    assert sorted(p.name for p in tmp_path.glob("cut.*")) == [], "no part file, no log left behind"


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_cut_picture_still_cuts_a_moving_picture(monkeypatch, tmp_path, ffmpeg):
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    real = _real_mp4(ffmpeg, tmp_path / "clip.mp4")
    out = tmp_path / "cut.mp4"
    assert video_creator.cut_picture(real, [(0.5, 1.5)], out) is True
    assert out.is_file() and video_creator._has_moving_picture(out)
    assert video_creator._probe_duration(out) == pytest.approx(1.0, abs=0.2)
