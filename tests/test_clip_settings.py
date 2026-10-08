"""Each clip records the narration settings it was made with (#p1-speed).

The project kept ONE stamp of the narration settings (``generation_speed``
and the three ElevenLabs values), written when a narration stage completed,
and ``get_slides_needing_regeneration`` told a settings change only by it: a
clip recorded its voice but not its speed. So every run that stamped the
settings without narrating every slide left slides at the old speed that
nothing redid - a **preview** at a new speed narrates only the slides inside
its budget and then stamps (found in this release's review); a slide whose
narration **failed** every attempt kept its previous clip under the new
stamp; before T2's fix, a **cancel**. Three patches on one cause is the
design smell this repo forbids, so the cause is fixed: ``SlideRenderState``
records ``audio_speed``, ``audio_stability``, ``audio_similarity_boost`` and
``audio_style`` with each clip (``update_slide_audio``), and the comparison
is made clip by clip, as the voice's already was. The stamp stays as the
record of what the last completed stage ran at.

Migration: a clip from before this release has no recorded settings and
counts as made at the stamped ones (nothing is redone on upgrade); the stamp
writes them into such clips before it moves, so a later preview or cancelled
run that stamps new settings can never make an old clip pass as made at them.

Kept as belt and braces, each with its own test elsewhere: a cancelled run
still does not stamp (``test_generate_cancel``), and a failed slide is still
marked and named in the closing line (``test_narration_failure``).
"""

import json
from pathlib import Path

import pytest

from core.project_manager import ProjectManager, SETTINGS_TOLERANCE
from services import file_item as file_item_module
from services import processing
from utils import config as config_module

from test_generate_cancel import (  # noqa: F401 - the fixtures are used by name
    ONE_FFMPEG, VOICE, _DeckItem, _deck_on_disk, _generate, _import_deck, _noise_mp3, _wait_end,
    admin, cast, isolated, needs_ffmpeg,
)
from test_narration_failure import _FlakyTTS, _reload

ONE_REAL_FFMPEG = pytest.mark.parametrize("ffmpeg", ONE_FFMPEG, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")


def _texts(tts) -> list:
    return sorted(c["text"] for c in tts.calls)


# ── through the route: the preview case ───────────────────────────────────────

@needs_ffmpeg
@ONE_REAL_FFMPEG
def test_a_preview_at_a_new_speed_leaves_the_slides_past_its_budget_to_the_next_full_render(
    admin, cast, monkeypatch, tmp_path, ffmpeg,
):
    """Three slides with clips at 1.0; a 1 s preview at 1.3 narrates slide 1
    only and stamps 1.3 (the review's probe). The full render at 1.3 then
    re-synthesises exactly slides 2 and 3 - the clips say 1.0 - and nothing
    else; afterwards every clip records 1.3."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(file_item_module, "FileItem", _DeckItem)
    pid = _import_deck(cast["admin_account"])
    pm = _deck_on_disk(ffmpeg, pid)  # clips made with no settings named: recorded as the stamp, 1.0
    assert [s.audio_speed for s in pm.state.slides] == [1.0, 1.0, 1.0]
    tts = _FlakyTTS(_noise_mp3(ffmpeg, tmp_path / "tts_clip.mp3", 1.0), lambda text: False)
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: tts)

    preview = _wait_end(admin, _generate(admin, pid, speed=1.3, preview_seconds=1), timeout=120)
    assert preview["status"] == "done" and preview["result"] == {"preview": "deck_preview.mp4"}, preview
    assert _texts(tts) == ["Slide 1 notes."], "the preview narrated the slide inside its budget only"
    after = _reload(pid)
    assert after.state.generation_speed == 1.3, "the preview's narration stage completed, so it is stamped"
    assert [s.audio_speed for s in after.state.slides] == [1.3, 1.0, 1.0]
    assert after.get_slides_needing_regeneration(current_speed=1.3) == [1, 2]

    tts.calls.clear()
    full = _wait_end(admin, _generate(admin, pid, speed=1.3), timeout=120)
    assert full["status"] == "done" and full["result"]["video"] == "deck.mp4", full
    assert _texts(tts) == ["Slide 2 notes.", "Slide 3 notes."], "exactly the slides the preview left out"
    assert all(c["speed"] == 1.3 for c in tts.calls)
    assert "kept an older clip" not in full["message"]
    redone = _reload(pid)
    assert [s.audio_speed for s in redone.state.slides] == [1.3, 1.3, 1.3]
    assert redone.get_slides_needing_regeneration(current_speed=1.3) == []


# ── the rule itself, on records ────────────────────────────────────────────────

def _project(tmp_path: Path, clips_at: list, stamp: float = 1.0) -> ProjectManager:
    """A deck project of ``len(clips_at)`` slides with notes, a clip each
    recorded at the given speed (None: a clip from before per-clip settings),
    and the project stamped ``stamp``."""
    pm = ProjectManager(tmp_path / "deck_project")
    pm.create_project(tmp_path / "deck.pptx", [f"Slide {i + 1} notes." for i in range(len(clips_at))], voice_id=VOICE)
    pm.state.generation_speed = stamp
    for i, speed in enumerate(clips_at):
        audio = pm.audio_dir / f"slide_{i + 1:03d}_audio.mp3"
        audio.write_bytes(b"mp3")
        pm.update_slide_audio(i, audio, 2.0, VOICE, speed=speed)
        if speed is None:
            pm.state.slides[i].audio_speed = None  # as an old record reads
    pm.save()
    return pm


def test_a_slide_whose_clip_records_another_speed_is_redone_whatever_the_stamp_says(tmp_path):
    """What a run that stamped without finishing leaves: the stamp at 1.5,
    two clips at 1.5 and two still at 1.0 (a cancel that stamped, a preview,
    two failed slides) - the two at 1.0 are redone, the two at 1.5 are not,
    and the mark plays no part in it."""
    pm = _project(tmp_path, [1.5, 1.0, 1.5, 1.0], stamp=1.5)
    assert [s.needs_regeneration for s in pm.state.slides] == [False] * 4
    assert pm.get_slides_needing_regeneration(current_speed=1.5) == [1, 3]
    assert pm.get_slides_needing_regeneration(current_speed=1.0) == [0, 2]
    # within the tolerance is the same speed; a setting not given is not compared
    assert pm.get_slides_needing_regeneration(current_speed=1.5 + SETTINGS_TOLERANCE / 2) == [1, 3]
    assert pm.get_slides_needing_regeneration(current_voice_id=VOICE) == []


def test_the_other_settings_are_judged_per_clip_too(tmp_path):
    pm = _project(tmp_path, [1.0, 1.0])
    pm.state.slides[1].audio_stability = 0.9
    pm.state.slides[0].audio_style = 0.4
    assert pm.get_slides_needing_regeneration(current_stability=0.5) == [1]
    assert pm.get_slides_needing_regeneration(current_style=0.0) == [0]
    assert pm.get_slides_needing_regeneration(current_similarity_boost=0.75) == []


def test_a_clip_from_before_this_release_counts_as_made_at_the_stamp_and_is_not_redone_on_upgrade(tmp_path):
    """An old record: clips with no recorded settings, the stamp at 1.0.
    Loaded, nothing is redone at 1.0, everything at 1.3; and the stamp moving
    to 1.3 with nothing synthesised (a preview's shape) first writes 1.0 into
    those clips, so they cannot pass as made at 1.3."""
    pm = _project(tmp_path, [None, None, None], stamp=1.0)
    raw = json.loads(pm.project_file.read_text(encoding="utf-8"))
    for slide in raw["slides"]:
        for key in ("audio_speed", "audio_stability", "audio_similarity_boost", "audio_style"):
            slide.pop(key, None)  # exactly what a record written before 0.14.1 holds
    pm.project_file.write_text(json.dumps(raw), encoding="utf-8")

    old = ProjectManager(pm.project_dir)
    old.load()
    assert [s.audio_speed for s in old.state.slides] == [None, None, None]
    assert [old.clip_setting(s, "speed") for s in old.state.slides] == [1.0, 1.0, 1.0]
    assert old.get_slides_needing_regeneration(current_speed=1.0, current_stability=0.5,
                                               current_similarity_boost=0.75, current_style=0.0) == []
    assert old.get_slides_needing_regeneration(current_speed=1.3) == [0, 1, 2]

    old.update_generation_settings(speed=1.3)
    assert old.state.generation_speed == 1.3
    assert [s.audio_speed for s in old.state.slides] == [1.0, 1.0, 1.0], "written in before the stamp moved"
    assert old.get_slides_needing_regeneration(current_speed=1.3) == [0, 1, 2], "not hidden by the new stamp"
    reloaded = ProjectManager(pm.project_dir)
    reloaded.load()
    assert [s.audio_speed for s in reloaded.state.slides] == [1.0, 1.0, 1.0], "and saved"


def test_a_clip_recorded_with_no_setting_named_takes_the_stamp_at_that_moment(tmp_path):
    """``update_slide_audio`` with no settings (the slide editor's callers,
    the test helpers) records what the project is working at, so a clip
    always says what it was made with."""
    pm = _project(tmp_path, [1.0], stamp=1.0)
    pm.state.generation_speed = 1.7
    pm.state.generation_style = 0.3
    audio = pm.audio_dir / "slide_001_audio.mp3"
    pm.update_slide_audio(0, audio, 2.0, VOICE)
    slide = pm.state.slides[0]
    assert (slide.audio_speed, slide.audio_stability, slide.audio_similarity_boost, slide.audio_style) == (1.7, 0.5, 0.75, 0.3)
