"""Downloading the narration transcript (T1): SRT, TXT or JSON, in two views.

``GET /api/projects/{pid}/transcript/download?format=…&view=…`` hands the
transcript over as a file. The owner's words: "would be good to be able to
download transcript for narration".

Two timing views, because two different questions get asked of a transcript:

- **timeline** - the narration AS THE RE-VOICE WILL SPEAK IT: projected
  through the edit, muted / wordless / past-the-end sentences left out, each
  sentence at its pin. Placed by the audition plan's own calls
  (``services.narration.project_narration``), never by a second projection or a
  second pin: ``test_the_timeline_view_is_placed_by_the_plans_own_numbers``
  holds the two together, and the source-inspection guard beside it holds the
  structure that makes that true.
- **source** - the script as it was spoken in the source video: every sentence
  at Whisper's own times, muted ones marked.

A read: allowed while a job holds the project, never audited, nothing written,
no ffmpeg or ffprobe.
"""

import json
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs, narration, processing
from services import projects as store
from utils import helpers
from utils.config import config

# Five sentences over a 12 s recording (tests/test_edit.py's), and a cut that
# removes 6.0-7.5: the third sentence starts inside the hole and goes with it;
# the second runs into the hole and is clamped at its edge.
SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "First sentence."},
    {"start": 5.0, "end": 7.0, "text": "Second sentence."},
    {"start": 6.5, "end": 7.4, "text": "Cut away."},
    {"start": 8.0, "end": 9.0, "text": "Fourth sentence."},
    {"start": 10.0, "end": 12.0, "text": "Fifth sentence."},
]
KEEP = [[0.0, 6.0], [7.5, 12.0]]  # output: 10.5 s

PASSWORD = "Editor-pass-12345"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(helpers, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(narration, "_BASELINE_CACHE", {})


@pytest.fixture(autouse=True)
def baseline(monkeypatch):
    """The plan - fetched beside the download by the anti-drift test - measures
    a speaking rate; stubbed at the engine's default, as
    tests/test_narration_plan.py stubs it. The download itself never measures
    anything."""
    monkeypatch.setattr(
        processing, "calibrate_tts_baseline",
        lambda segments, tts_gen, tmp_dir, *, provider, voice_id, progress=None, file_label="":
            processing.DEFAULT_BASELINE_RATE,
    )


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _wav(pid: str, seconds: float) -> None:
    """The file the transcribe step leaves beside the video, at the length the
    timeline is bounded by (1 kHz so the header arithmetic is exact)."""
    path = store.PROJECTS_DIR / pid / "audio.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(1000)
        wf.writeframes(np.zeros(int(round(seconds * 1000)), dtype="<i2").tobytes())


def _video(segments=None, *, audio_seconds: float | None = 12.0, filename: str = "clip.mp4") -> str:
    pid = store.import_upload(filename, b"video-bytes")["id"]
    store.set_transcript(pid, [dict(s) for s in (SEGMENTS if segments is None else segments)])
    if audio_seconds is not None:
        _wav(pid, audio_seconds)
    return pid


def _text(pid: str, fmt: str, view: str) -> str:
    return narration.export_transcript(pid, fmt, view)[2].decode("utf-8")


def _json(pid: str, view: str) -> dict:
    return json.loads(_text(pid, "json", view))


def _download(client: TestClient, pid: str, **params):
    return client.get(f"/api/projects/{pid}/transcript/download", params=params or None)


def _rename(pid: str, name: str) -> None:
    record = store.get_project(pid)
    record["name"] = name
    store.save_project(record)


# ── every format, both views ─────────────────────────────────────────────────

@pytest.mark.parametrize("view", ["timeline", "source"])
@pytest.mark.parametrize("fmt", ["srt", "txt", "json"])
def test_every_format_in_every_view_is_a_named_utf8_file(fmt, view):
    pid = _video()

    filename, media_type, body = narration.export_transcript(pid, fmt, view)
    assert filename == f"clip-narration.{fmt}"
    assert media_type == narration.EXPORT_FORMATS[fmt][0]
    assert not body.startswith(b"\xef\xbb\xbf"), "UTF-8 with no BOM"
    text = body.decode("utf-8")
    assert "First sentence." in text and "Fifth sentence." in text


# ── srt ──────────────────────────────────────────────────────────────────────

def test_the_srt_is_numbered_from_one_with_srt_clocks_and_a_blank_line_between_cues():
    pid = _video()

    assert _text(pid, "srt", "source") == (
        "1\n00:00:00,000 --> 00:00:02,000\nFirst sentence.\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nSecond sentence.\n\n"
        "3\n00:00:06,500 --> 00:00:07,400\nCut away.\n\n"
        "4\n00:00:08,000 --> 00:00:09,000\nFourth sentence.\n\n"
        "5\n00:00:10,000 --> 00:00:12,000\nFifth sentence.\n"
    )


def test_srt_clocks_carry_hours_and_both_clocks_round_to_whole_milliseconds_first():
    assert narration.srt_time(3725.15) == "01:02:05,150"
    assert narration.srt_time(0) == "00:00:00,000"
    assert narration.srt_time(1.9996) == "00:00:02,000", "not 00:00:01,1000"
    assert narration.srt_time(-3.0) == "00:00:00,000", "clamped, as a pin is"
    # The List view's own label, m:ss.mmm.
    assert narration.timecode(65.25) == "1:05.250"
    assert narration.timecode(0.0) == "0:00.000"
    assert narration.timecode(59.9996) == "1:00.000", "not 0:60.000"
    assert narration.timecode(725.1) == "12:05.100"


def test_a_zero_length_or_reversed_span_is_given_half_a_second_so_a_player_shows_it():
    """An SRT rule only: the other two formats carry the span as stored - the
    floor is a subtitle player's need, not a fact about the sentence.

    A span before or across zero (the whole-list Save does not bound a time at
    zero) is clamped FIRST and floored after: floored on the raw seconds, it
    satisfied the floor and was then shrunk by the two clocks' clamps to
    nothing, or to less than the floor."""
    pid = _video([
        {"start": -2.0, "end": -1.0, "text": "Entirely before zero."},
        {"start": -1.0, "end": 0.2, "text": "Crosses zero."},
        {"start": -0.3, "end": 0.1, "text": "Crosses zero, short."},
        {"start": 3600.0, "end": 3600.0, "text": "An instant, an hour in."},
        {"start": 3610.0, "end": 3609.2, "text": "Ends before it starts."},
        {"start": 3620.0, "end": 3620.3, "text": "Shorter than the floor."},
        {"start": 3630.0, "end": 3630.5, "text": "Exactly the floor."},
    ], audio_seconds=None)

    srt = _text(pid, "srt", "source")
    assert srt.startswith(
        "1\n00:00:00,000 --> 00:00:00,500\nEntirely before zero.\n\n"
        "2\n00:00:00,000 --> 00:00:00,500\nCrosses zero.\n\n"
        "3\n00:00:00,000 --> 00:00:00,500\nCrosses zero, short.\n\n"
    )
    assert "01:00:00,000 --> 01:00:00,500" in srt
    assert "01:00:10,000 --> 01:00:10,500" in srt
    assert "01:00:20,000 --> 01:00:20,500" in srt
    assert "01:00:30,000 --> 01:00:30,500" in srt
    spans = [(s["start"], s["end"]) for s in _json(pid, "source")["sentences"]]
    assert spans == [
        (-2.0, -1.0), (-1.0, 0.2), (-0.3, 0.1),
        (3600.0, 3600.0), (3610.0, 3609.2), (3620.0, 3620.3), (3630.0, 3630.5),
    ]


def test_the_srt_leaves_a_wordless_sentence_out_and_keeps_its_numbering_contiguous():
    """A cue with no text is one most players skip or choke on. The text and
    JSON files keep the sentence: its timing is still a fact of the
    transcript."""
    pid = _video([
        {"start": 0.0, "end": 2.0, "text": "Spoken."},
        {"start": 3.0, "end": 4.0, "text": "   "},
        {"start": 5.0, "end": 6.0, "text": "", "muted": True},
        {"start": 7.0, "end": 9.0, "text": "Spoken too."},
    ])

    assert _text(pid, "srt", "source") == (
        "1\n00:00:00,000 --> 00:00:02,000\nSpoken.\n\n"
        "2\n00:00:07,000 --> 00:00:09,000\nSpoken too.\n"
    )
    txt = _text(pid, "txt", "source")
    assert "\n[0:03.000]\n" in txt and "\n[0:05.000]  [muted]\n" in txt
    assert [s["text"] for s in _json(pid, "source")["sentences"]] == ["Spoken.", "   ", "", "Spoken too."]


def test_a_newline_inside_a_sentence_is_one_line_in_the_srt_and_txt_and_kept_in_the_json():
    pid = _video([{"start": 0.0, "end": 2.0, "text": "First line\nsecond   line."}])

    assert "\nFirst line second line.\n" in _text(pid, "srt", "source")
    assert "[0:00.000]  First line second line.\n" in _text(pid, "txt", "source")
    assert _json(pid, "source")["sentences"][0]["text"] == "First line\nsecond   line."


# ── txt ──────────────────────────────────────────────────────────────────────

def test_the_txt_has_a_two_line_header_and_one_timecoded_line_per_sentence():
    pid = _video()
    narration.update_segment(pid, 1, muted=True)

    assert _text(pid, "txt", "source") == (
        "# clip — narration (source)\n"
        f"# {narration.EXPORT_VIEWS['source']}\n"
        "\n"
        "[0:00.000]  First sentence.\n"
        "[0:05.000]  [muted] Second sentence.\n"
        "[0:06.500]  Cut away.\n"
        "[0:08.000]  Fourth sentence.\n"
        "[0:10.000]  Fifth sentence.\n"
    )
    timeline = _text(pid, "txt", "timeline")
    assert timeline.startswith(
        "# clip — narration (timeline)\n"
        "# timings are where each sentence is aimed; a sentence whose clip runs long "
        "is placed later by the render\n"
        "\n"
        "[0:00.000]  First sentence.\n"
    )
    assert "[muted]" not in timeline and "Second sentence." not in timeline


# ── json ─────────────────────────────────────────────────────────────────────

def test_the_json_carries_the_project_the_view_the_length_the_note_and_each_sentence():
    pid = _video()
    narration.update_segment(pid, 1, muted=True)
    narration.update_segment(pid, 3, offset=0.25)

    source = _json(pid, "source")
    assert list(source) == ["project", "view", "duration", "note", "sentences"]
    assert (source["project"], source["view"], source["duration"]) == ("clip", "source", 12.0)
    assert source["note"] == narration.EXPORT_VIEWS["source"]
    assert source["sentences"][1] == {
        "index": 1, "start": 5.0, "end": 7.0, "text": "Second sentence.", "muted": True, "offset": None,
    }
    assert source["sentences"][3] == {
        "index": 3, "start": 8.0, "end": 9.0, "text": "Fourth sentence.", "muted": False, "offset": 0.25,
    }

    timeline = _json(pid, "timeline")
    assert timeline["note"] == narration.EXPORT_VIEWS["timeline"]
    assert [s["index"] for s in timeline["sentences"]] == [0, 2, 3, 4], "the muted one is not in the narration"
    assert timeline["sentences"][2] == {"index": 3, "start": 8.25, "end": 9.25, "text": "Fourth sentence.", "offset": 0.25}
    assert all("muted" not in s for s in timeline["sentences"]), "nothing in the timeline view is muted"

    # Two-space indent, the words written as themselves (ensure_ascii=False).
    assert _text(pid, "json", "source").startswith('{\n  "project": "clip",\n  "view": "source",\n')


def test_an_em_dash_and_a_non_latin_sentence_survive_every_format():
    pid = _video([
        {"start": 0.0, "end": 2.0, "text": "Wait — then go."},
        {"start": 3.0, "end": 5.0, "text": "こんにちは、世界。"},
    ])

    for fmt in narration.EXPORT_FORMATS:
        text = _text(pid, fmt, "source")
        assert "Wait — then go." in text and "こんにちは、世界。" in text, fmt
    assert "\\u" not in _text(pid, "json", "source"), "ensure_ascii=False"


# ── the timeline view: as the re-voice will speak it ─────────────────────────

def test_the_timeline_view_places_each_sentence_at_its_pin_for_its_spoken_length():
    pid = _video()
    narration.update_segment(pid, 1, offset=0.5)    # aimed later
    narration.update_segment(pid, 3, offset=-0.5)   # aimed earlier

    placed = [(s["index"], s["start"], s["end"]) for s in _json(pid, "timeline")["sentences"]]
    assert placed == [(0, 0.0, 2.0), (1, 5.5, 7.5), (2, 6.5, 7.4), (3, 7.5, 8.5), (4, 10.0, 12.0)]
    # ... while the source view keeps them where they were spoken, the offset beside each.
    spoken = [(s["start"], s["offset"]) for s in _json(pid, "source")["sentences"]]
    assert spoken == [(0.0, None), (5.0, 0.5), (6.5, None), (8.0, -0.5), (10.0, None)]


def test_a_pin_pulled_before_zero_is_floored_as_the_render_floors_it():
    pid = _video()
    narration.update_segment(pid, 0, offset=-3.0)

    first = _json(pid, "timeline")["sentences"][0]
    assert (first["start"], first["end"]) == (0.0, 2.0)


def test_the_timeline_view_is_the_narration_projected_through_the_cut(client):
    """A sentence whose start falls in the hole is not in the narration; every
    later one is earlier by the hole's length; the picture's length bounds the
    file. The source view is the transcript as stored, untouched."""
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": KEEP}).status_code == 200

    timeline = _json(pid, "timeline")
    assert timeline["duration"] == 10.5
    assert [(s["index"], s["start"], s["end"]) for s in timeline["sentences"]] == [
        (0, 0.0, 2.0), (1, 5.0, 6.0), (3, 6.5, 7.5), (4, 8.5, 10.5),
    ]
    assert "Cut away." not in _text(pid, "srt", "timeline")

    source = _json(pid, "source")
    assert source["duration"] == 12.0
    assert [(s["index"], s["start"], s["end"]) for s in source["sentences"]] == [
        (0, 0.0, 2.0), (1, 5.0, 7.0), (2, 6.5, 7.4), (3, 8.0, 9.0), (4, 10.0, 12.0),
    ]


def test_a_cut_to_the_picture_alone_moves_no_sentence_but_bounds_the_file_by_the_pictures_length(client):
    """E3's locked narration: the video list cuts the picture, the sentences
    stay where they were spoken - and one pinned past the shorter picture is
    dropped, as the ``-shortest`` mux drops it."""
    pid = _video()
    r = client.put(f"/api/projects/{pid}/edit", json={"video": [[0.0, 9.5]]})
    assert r.status_code == 200, r.text

    timeline = _json(pid, "timeline")
    assert timeline["duration"] == 9.5
    assert [(s["index"], s["start"], s["end"]) for s in timeline["sentences"]] == [
        (0, 0.0, 2.0), (1, 5.0, 7.0), (2, 6.5, 7.4), (3, 8.0, 9.0),
    ], "the fifth sentence is pinned at 10 s, past the 9.5 s picture"


def test_a_sentence_pinned_at_or_past_the_end_is_left_out_as_the_render_drops_it():
    pid = _video()
    narration.update_segment(pid, 4, offset=5.0)   # 15 s into a 12 s video
    narration.update_segment(pid, 3, offset=4.0)   # exactly 12.0: AT the end is past it

    assert [s["index"] for s in _json(pid, "timeline")["sentences"]] == [0, 1, 2]
    # Still in the source view, with the offset that put it there.
    tail = [(s["index"], s["offset"]) for s in _json(pid, "source")["sentences"]][3:]
    assert tail == [(3, 4.0), (4, 5.0)]


def test_a_sentence_running_past_the_end_ends_at_the_pictures_length():
    pid = _video()
    narration.update_segment(pid, 4, offset=1.0)   # 11.0-13.0 in a 12 s video

    last = _json(pid, "timeline")["sentences"][-1]
    assert (last["start"], last["end"]) == (11.0, 12.0)
    assert "00:00:11,000 --> 00:00:12,000" in _text(pid, "srt", "timeline")


def test_a_muted_or_wordless_sentence_is_out_of_the_timeline_view_and_in_the_source_view():
    pid = _video([
        {"start": 0.0, "end": 2.0, "text": "Spoken."},
        {"start": 3.0, "end": 4.0, "text": "Muted.", "muted": True},
        {"start": 5.0, "end": 6.0, "text": "   "},
        {"start": 7.0, "end": 9.0, "text": "Spoken too."},
    ])

    assert [s["index"] for s in _json(pid, "timeline")["sentences"]] == [0, 3]
    source = [(s["index"], s["muted"], s["text"]) for s in _json(pid, "source")["sentences"]]
    assert source == [(0, False, "Spoken."), (1, True, "Muted."), (2, False, "   "), (3, False, "Spoken too.")]
    assert "\n[muted] Muted.\n" in _text(pid, "srt", "source")
    assert "[muted]" not in _text(pid, "srt", "timeline")


def test_a_segment_that_is_not_even_a_dictionary_is_skipped_rather_than_a_500(client):
    pid = _video()
    record = store.get_project(pid)
    record["transcript"] = [{"start": 0.0, "end": 2.0, "text": "Fine."}, "not a segment"]
    store.save_project(record)

    for view in narration.EXPORT_VIEWS:
        r = _download(client, pid, format="json", view=view)
        assert r.status_code == 200, r.text
        assert [s["index"] for s in r.json()["sentences"]] == [0], view


# ── one projection, one pin: the download is placed by the plan's numbers ────

def test_the_timeline_view_is_placed_by_the_plans_own_numbers(client):
    """The anti-drift pin. The sentences the download lists are exactly the
    ones the plan says the render will speak, at exactly the plan's
    ``pinned_start`` and for the plan's own spoken length - with an offset, a
    mute, a sentence nudged off the end and a narration cut all in play. A
    second projection or a second pin on either side fails here."""
    pid = _video(SEGMENTS + [{"start": 11.0, "end": 11.8, "text": "Off the end once nudged."}])
    r = client.put(f"/api/projects/{pid}/edit", json={"video": [[0.0, 12.0]], "narration": KEEP})
    assert r.status_code == 200, r.text
    for index, changes in ((1, {"offset": 0.4}), (3, {"muted": True}), (5, {"offset": 3.0})):
        assert client.patch(f"/api/projects/{pid}/transcript/{index}", json=changes).status_code == 200

    plan = client.get(f"/api/projects/{pid}/narration/plan").json()
    spoken = [s for s in plan["sentences"] if s["speakable"]]
    assert [s["index"] for s in spoken] == [0, 1, 4], "one cut, one muted, one off the end"

    download = _json(pid, "timeline")
    assert download["duration"] == plan["duration"]
    assert [(s["index"], s["start"]) for s in download["sentences"]] == [
        (s["index"], s["pinned_start"]) for s in spoken
    ]
    assert [s["end"] - s["start"] for s in download["sentences"]] == pytest.approx(
        [s["end"] - s["start"] for s in spoken]
    )


def test_the_download_and_the_plan_share_one_projection_and_one_pin():
    """The structure behind the pin above: ``edit.apply`` is called in ONE
    place in the module - ``project_narration`` - and both the plan and the
    download's timeline view go through it. The download never touches the
    projection, the pin or the past-the-end rule itself."""
    import inspect

    assert "edit.apply(" in inspect.getsource(narration.project_narration)
    assert "sentence_window(" in inspect.getsource(narration.project_narration)
    for caller in (narration.plan, narration._timeline_cues, narration.export_transcript):
        source = inspect.getsource(caller)
        assert "edit.apply(" not in source, caller.__name__
    for reader in (narration._timeline_cues, narration._source_cues, narration.export_transcript):
        source = inspect.getsource(reader)
        assert "_pin(" not in source and "sentence_window(" not in source, reader.__name__
    assert "project_narration(" in inspect.getsource(narration.plan)
    assert "project_narration(" in inspect.getsource(narration._timeline_cues)
    # ... and over the WHOLE module, not only the functions named above, so a
    # second call site in a brand-new function cannot slip in.
    module = inspect.getsource(narration)
    assert module.count("edit.apply(") == 1, "a second projection has appeared in services/narration.py"
    assert module.count("sentence_window(") == 1, "a second window pass has appeared in services/narration.py"
    assert "def project_transcript" not in module
    assert "def _pin" not in module


# ── the route ────────────────────────────────────────────────────────────────

def test_the_route_defaults_to_the_timeline_srt_named_after_the_project_and_uncacheable(client):
    pid = _video(filename="My great talk.mp4")

    r = _download(client, pid)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/x-subrip"
    assert r.headers["content-disposition"] == 'attachment; filename="My great talk-narration.srt"'
    # The transcript is rewritten in place, so the browser must ask every time
    # (the same header the re-voiced video and the tracks carry).
    assert r.headers["cache-control"] == "no-cache"
    assert r.content == narration.export_transcript(pid, "srt", "timeline")[2]
    assert r.content.startswith(b"1\n00:00:00,000 --> 00:00:02,000\nFirst sentence.\n\n")


@pytest.mark.parametrize("view", ["timeline", "source"])
@pytest.mark.parametrize("fmt", ["srt", "txt", "json"])
def test_each_format_and_view_is_asked_for_by_query_parameter(client, fmt, view):
    pid = _video()

    r = _download(client, pid, format=fmt, view=view)
    assert r.status_code == 200, r.text
    media_type, extension = narration.EXPORT_FORMATS[fmt]
    assert r.headers["content-type"] == media_type
    assert r.headers["content-disposition"] == f'attachment; filename="clip-narration.{extension}"'
    assert r.headers["cache-control"] == "no-cache"
    assert r.content == narration.export_transcript(pid, fmt, view)[2]
    if fmt == "json":
        assert r.json()["view"] == view


def test_the_filename_is_the_projects_name_made_safe(client):
    """Spaces stay; the characters no filesystem takes become underscores -
    ``utils.helpers.sanitize_filename``, the rule the engine names its own
    outputs by."""
    pid = _video()
    _rename(pid, 'Take 2: "final"/v1?')

    r = _download(client, pid, format="txt")
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == 'attachment; filename="Take 2_ _final__v1_-narration.txt"'
    assert narration.export_transcript(pid, "json", "source")[0] == "Take 2_ _final__v1_-narration.json"


def test_a_non_ascii_name_travels_as_rfc_5987_beside_an_ascii_fallback(client):
    """A header is Latin-1: "Présentation" would be a 500 written as itself."""
    pid = _video()
    _rename(pid, "Présentation")

    r = _download(client, pid)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == (
        "attachment; filename=\"Presentation-narration.srt\"; filename*=utf-8''Pr%C3%A9sentation-narration.srt"
    )


@pytest.mark.parametrize("name, fallback, encoded", [
    # U+FF02 FULLWIDTH QUOTATION MARK folds to a quote - an unescaped one
    # inside a quoted-string is not a header - so the fold is sanitised again.
    ("a＂b", "a_b", "a%EF%BC%82b"),
    # U+FF3C FULLWIDTH REVERSE SOLIDUS folds to a backslash: a quoted-pair.
    ("c＼d", "c_d", "c%EF%BC%BCd"),
    # U+FF0F FULLWIDTH SOLIDUS folds to a slash.
    ("e／f", "e_f", "e%EF%BC%8Ff"),
    # A wholly non-Latin name folds away to nothing: a legacy client would
    # otherwise save "-narration.srt".
    ("日本語", "transcript", "%E6%97%A5%E6%9C%AC%E8%AA%9E"),
])
def test_a_fold_that_makes_a_quote_a_backslash_or_nothing_is_still_a_valid_header(client, name, fallback, encoded):
    pid = _video()
    _rename(pid, name)

    r = _download(client, pid)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == (
        f"attachment; filename=\"{fallback}-narration.srt\"; filename*=utf-8''{encoded}-narration.srt"
    )


@pytest.mark.parametrize("name, header", [
    ("line\nSet-Cookie: a=b", 'attachment; filename="lineSet-Cookie_ a=b-narration.srt"'),
    ("cr\rname", 'attachment; filename="crname-narration.srt"'),
    ("nul\x00name\x7f", 'attachment; filename="nulname-narration.srt"'),
    ("Prés\nentation",
     "attachment; filename=\"Presentation-narration.srt\"; filename*=utf-8''Pr%C3%A9sentation-narration.srt"),
])
def test_a_control_character_in_the_name_never_reaches_the_header(client, name, header):
    """A hand-edited record: h11 refuses a header carrying one and uvicorn
    drops the connection, so the download would be a closed socket."""
    pid = _video()
    _rename(pid, name)

    r = _download(client, pid)
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == header
    assert not any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in r.headers["content-disposition"])


def test_an_unknown_format_or_view_is_refused_before_anything_is_read(client):
    pid = _video()

    assert _download(client, pid, format="vtt").status_code == 422
    assert _download(client, pid, view="render").status_code == 422
    with pytest.raises(ValueError, match="Unknown transcript format"):
        narration.export_transcript(pid, "vtt", "timeline")
    with pytest.raises(ValueError, match="Unknown transcript view"):
        narration.export_transcript(pid, "srt", "render")


def test_an_untranscribed_video_has_nothing_to_download(client):
    pid = store.import_upload("other.mp4", b"video-bytes")["id"]
    r = _download(client, pid)
    assert r.status_code == 404 and "transcribe it first" in r.json()["detail"]


def test_a_deck_has_no_transcript_to_download(client):
    deck = store.import_upload("deck.pptx", b"pptx-bytes")["id"]
    r = _download(client, deck)
    assert r.status_code == 400 and "Only video projects" in r.json()["detail"]


def test_a_missing_project_is_a_404(client):
    assert _download(client, "aabbccddeeff").status_code == 404


def test_the_source_view_needs_no_edit_and_the_timeline_view_says_when_the_edit_cannot_be_measured(client):
    """The audio removed by hand after a cut was made: the timeline view cannot
    be placed (the edit cannot be measured - the plan's own 400), while the
    source view has nothing to project and still answers."""
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": KEEP}).status_code == 200
    (store.PROJECTS_DIR / pid / "audio.wav").unlink()

    r = _download(client, pid, view="timeline")
    assert r.status_code == 400 and "extracted audio is missing" in r.json()["detail"]
    r = _download(client, pid, format="json", view="source")
    assert r.status_code == 200, r.text
    assert r.json()["duration"] == 12.0, "the transcript's own end, with no WAV and no record duration"


# ── a read ───────────────────────────────────────────────────────────────────

def test_the_download_is_allowed_while_a_job_holds_the_project_and_writes_nothing(client, monkeypatch):
    """It writes nothing, so refusing it while a re-voice runs would be a 409
    on a read. The project directory is byte-for-byte what it was."""
    pid = _video()
    pdir = store.PROJECTS_DIR / pid
    before = {p.name: p.read_bytes() for p in pdir.iterdir()}
    monkeypatch.setattr(
        jobs, "active_for",
        lambda project_id: {"id": "j1", "kind": "revoice", "status": "running"} if project_id == pid else None,
    )

    for view in narration.EXPORT_VIEWS:
        assert _download(client, pid, format="json", view=view).status_code == 200, view
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": 1.0}).status_code == 409
    assert {p.name: p.read_bytes() for p in pdir.iterdir()} == before


def test_no_ffmpeg_or_ffprobe_runs_for_a_download(client, monkeypatch):
    """No probe and no decode: the lengths are the WAV header's, the pins are
    arithmetic. Every way of starting a process is made to fail."""
    import subprocess

    def _refuse(*args, **kwargs):
        raise AssertionError(f"a subprocess was started for a download: {args[:1]}")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _refuse)
    pid = _video()

    for fmt in narration.EXPORT_FORMATS:
        for view in narration.EXPORT_VIEWS:
            assert _download(client, pid, format=fmt, view=view).status_code == 200, (fmt, view)


def test_another_editor_is_refused_and_the_owner_and_an_admin_are_not(client):
    """``readable_project``: 403 for anyone but the owner or an admin, before
    the transcript is so much as looked at (the sweep in
    tests/test_project_ownership.py names the route too)."""
    from api.app import app

    owner = auth_store.create_user("owner", PASSWORD, "Olive Owner", role="editor", must_change_password=False)
    auth_store.create_user("other", PASSWORD, "Otto Other", role="editor", must_change_password=False)
    pid = _video()
    record = store.get_project(pid)
    record["owner_id"] = owner["id"]
    store.save_project(record)

    def signed_in(username: str) -> TestClient:
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
        return c

    r = _download(signed_in("other"), pid)
    assert r.status_code == 403 and "admin or the project's owner" in r.json()["detail"]
    assert _download(signed_in("owner"), pid).status_code == 200
    assert _download(client, pid).status_code == 200, "the admin"


def test_the_download_is_a_get_so_the_audit_guard_need_not_name_it(routes):
    methods = {r.method for r in routes if r.path.endswith("/transcript/download")}
    assert methods == {"GET"}, methods
