"""A slide whose narration fails every attempt is redone by the next render (#p1-speed).

``_generate_audio_parallel`` tries a slide's narration three times; after the
third failure it returned None and the slide kept its previous clip. The
stage then completed, so ``update_generation_settings(speed=...)`` stamped
this run's settings on the project - and a changed Speed was recorded as
applied although that one slide still carried the old one. A clip records
its voice, not its speed (the T2 finding, which fixed the cancel case only),
so ``get_slides_needing_regeneration`` saw nothing to redo: the slide stayed
at the old speed on every later render.

Now a slide whose synthesis failed is marked ``needs_regeneration`` as its
failure is counted, so the next render redoes it whatever the settings, and
the render's closing line names how many slides kept an older clip.

A route-level test, through the real route, the real ``VideoProcessor`` and
the real deck render on one real ffmpeg, over a deck whose clips are already
there (made at speed 1.0), with a voice service that fails one slide every
time - so the test takes the back-off (3 s, then 6 s) once.
"""

import hashlib
import shutil
from pathlib import Path

import pytest

from core.project_manager import ProjectManager, get_project_dir
from services import file_item as file_item_module
from services import processing
from services import projects as store
from utils import config as config_module

from test_generate_cancel import (  # noqa: F401 - the fixtures are used by name
    ONE_FFMPEG, _DeckItem, _deck_on_disk, _generate, _import_deck, _noise_mp3, _wait_end,
    admin, cast, isolated, needs_ffmpeg,
)

ONE_REAL_FFMPEG = pytest.mark.parametrize("ffmpeg", ONE_FFMPEG, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")


class _FlakyTTS:
    """The voice service: copies a prepared clip to the slide's audio path,
    except for the slides ``fails(text)`` names, which get nothing (None, as
    a provider answers when its service fails); records every call with the
    speed it was asked for."""

    def __init__(self, clip: Path, fails):
        self.clip = clip
        self.fails = fails
        self.calls: list = []

    def generate_audio(self, text, voice_id, output_path, speed=1.0, **kwargs):
        self.calls.append({"text": text, "speed": speed})
        if self.fails(text):
            return None
        shutil.copy2(self.clip, output_path)
        return Path(output_path)


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _reload(pid: str) -> ProjectManager:
    project_dir = store.PROJECTS_DIR / pid
    pm = ProjectManager(get_project_dir(project_dir / "deck.pptx", project_dir))
    pm.load()
    return pm


@needs_ffmpeg
@ONE_REAL_FFMPEG
def test_a_slide_whose_narration_fails_keeps_its_clip_and_is_redone_by_the_next_render(
    admin, cast, monkeypatch, tmp_path, ffmpeg,
):
    """Render at a new speed with slide 2's narration failing every time: the
    job finishes with the closing line naming the slide, the slide keeps its
    old clip and is marked, the saved speed is the new one - and the next
    render re-synthesises exactly that slide, at the new speed."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(file_item_module, "FileItem", _DeckItem)
    pid = _import_deck(cast["admin_account"])
    pm = _deck_on_disk(ffmpeg, pid)  # three slides, a clip each, at the project's speed of 1.0
    assert pm.state.generation_speed == 1.0
    old_clips = {i: _sha(pm.state.slides[i].audio_path) for i in range(3)}

    # A clip of its own length, so a re-synthesised slide's file differs from the deck's
    failing = {"on": True}
    tts = _FlakyTTS(_noise_mp3(ffmpeg, tmp_path / "tts_clip.mp3", 2.5),
                    lambda text: failing["on"] and text.startswith("Slide 2"))
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: tts)

    # -- the render at the new speed, slide 2 failing three times -------------
    job = _wait_end(admin, _generate(admin, pid, speed=1.3), timeout=120)
    assert job["status"] == "done" and job["result"]["video"] == "deck.mp4", job
    assert "1 slide (2) kept an older clip" in job["message"], job["message"]
    assert "Render again to redo it" in job["message"], job["message"]
    # The result names the slide as the line does (1-based): the Generate
    # card keeps the closing line on its account (lib/jobs.ts lineToKeep).
    assert job["result"]["failed_slides"] == [2], job["result"]

    attempts = [c for c in tts.calls if c["text"].startswith("Slide 2")]
    assert len(attempts) == 3 and all(c["speed"] == 1.3 for c in attempts), tts.calls
    assert sorted(c["text"] for c in tts.calls if not c["text"].startswith("Slide 2")) == ["Slide 1 notes.", "Slide 3 notes."]

    after = _reload(pid)
    assert after.state.generation_speed == 1.3, "the stage completed, so the new speed is stamped"
    flags = [s.needs_regeneration for s in after.state.slides]
    assert flags == [False, True, False], f"only the failed slide is marked: {flags}"
    assert _sha(after.state.slides[1].audio_path) == old_clips[1], "slide 2 kept its old clip"
    assert _sha(after.state.slides[0].audio_path) != old_clips[0], "slide 1 was re-synthesised"
    assert after.get_slides_needing_regeneration(current_speed=1.3) == [1]

    # -- the next render, the voice service well again ------------------------
    failing["on"] = False
    tts.calls.clear()
    job = _wait_end(admin, _generate(admin, pid, speed=1.3), timeout=120)
    assert job["status"] == "done" and job["result"]["video"] == "deck.mp4", job
    assert "kept an older clip" not in job["message"], job["message"]
    assert "failed_slides" not in job["result"], "nothing failed: no such key, so the card keeps no line"
    assert tts.calls == [{"text": "Slide 2 notes.", "speed": 1.3}], "exactly the failed slide, at the new speed"

    redone = _reload(pid)
    assert [s.needs_regeneration for s in redone.state.slides] == [False, False, False]
    assert _sha(redone.state.slides[1].audio_path) != old_clips[1], "slide 2 has its new clip"
    assert redone.get_slides_needing_regeneration(current_speed=1.3) == []


@needs_ffmpeg
@ONE_REAL_FFMPEG
def test_a_preview_whose_narration_fails_a_slide_names_it_in_its_result_too(admin, cast, monkeypatch, tmp_path, ffmpeg):
    """A 1 s preview narrates slide 1 only; with its narration failing every
    time, the preview still completes (the old clip) and its result names
    the slide, so the card keeps the line for a preview as for a render."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(file_item_module, "FileItem", _DeckItem)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    tts = _FlakyTTS(_noise_mp3(ffmpeg, tmp_path / "tts_clip.mp3", 1.0), lambda text: text.startswith("Slide 1"))
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: tts)

    job = _wait_end(admin, _generate(admin, pid, speed=1.3, preview_seconds=1), timeout=120)
    assert job["status"] == "done", job
    assert job["result"] == {"preview": "deck_preview.mp4", "failed_slides": [1]}, job["result"]
    assert "1 slide (1) kept an older clip" in job["message"], job["message"]
    assert [c["text"] for c in tts.calls] == ["Slide 1 notes."] * 3, "the one slide inside the budget, three attempts"


def test_the_closing_note_names_how_many_slides_kept_an_older_clip():
    assert processing.narration_failure_note([]) == ""
    assert processing.narration_failure_note([3]) == (
        "; 1 slide (4) kept an older clip: its narration failed after 3 attempts. Render again to redo it."
    )
    assert processing.narration_failure_note([5, 1, 1]) == (
        "; 2 slides (2 and 6) kept an older clip: their narration failed after 3 attempts. Render again to redo them."
    )
