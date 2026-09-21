"""The music library (porting vertical 6, phase E4a): ``services.music`` and
the five studio-wide routes in ``api/routers/music.py``.

What these tests hold in place:

- a name is sanitised once, at upload, and is then immutable and unique - an
  existing name is a 409, never a replacement, because a clip refers to a
  file by name (spec §12.3) - and unique CASE-INSENSITIVELY, while every
  lookup is exact: a name that differs from a stored one only by case is a
  404, never the other file served or deleted;
- the file is DECODED once, at upload, behind the one seam
  (``decode_audio``), to prove it is audio and to measure it; the peaks the
  lane draws are cached then, with the waveform strip's own arithmetic; and
  nothing here ever runs ffprobe (traps 2 and 30);
- every write is a ``.part`` published atomically and a refused upload
  leaves nothing behind;
- a delete is refused, naming the projects, while any edit names the file
  (trap 25);
- the routes need a session but no owner (the library is studio-wide), a
  crafted name never reaches a file outside the directory, and both
  mutations are audited.

The decode is faked for the unit tests; the integration tests run the real
seam through the resolved ffmpeg when there is one - including what a
refusal says, which names the upload and never a path on the server.
"""

import json
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import music, waveform
from services import projects as store
from utils import config as config_module
from utils.config import config

# The fake decoder's answer for anything that is not marked as noise: 2.5 s
# of stereo at 1 kHz, mixed down to these 2500 samples.
RATE = 1000
rng = np.random.default_rng(11)
SIGNAL = rng.integers(-32768, 32767, size=2500, dtype=np.int64).astype(np.int16)
NOISE = b"NOT-AUDIO"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(config_module, "TEMP_DIR", tmp_path / "temp")


@pytest.fixture
def decodes(monkeypatch):
    """``decode_audio`` faked: records the paths it was handed, refuses a body
    that starts with NOISE, and answers SIGNAL for anything else."""
    calls: list[Path] = []

    def fake(path, display_name=None):
        calls.append(Path(path))
        if Path(path).read_bytes().startswith(NOISE):
            raise music.NotAudio(f"faked: {display_name or Path(path).name} is not audio")
        return music.Decoded(2.5, RATE, 2, SIGNAL)

    monkeypatch.setattr(music, "decode_audio", fake)
    return calls


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _upload(client, name, data=b"mp3-bytes", media_type="audio/mpeg"):
    return client.post("/api/music", files={"file": (name, data, media_type)})


def _rows(action: str) -> list[dict]:
    return [r for r in auth_store.list_audit(limit=50) if r["action"] == action]


def _project_using(name: str, project_name: str, *, clips=None) -> str:
    """A video project whose edit has a clip on ``name``."""
    pid = store.import_upload(f"{project_name}.mp4", b"video-bytes")["id"]
    record = store.get_project(pid)
    record["name"] = project_name
    record["edit"] = {"version": 2, "music": clips if clips is not None else [
        {"id": "m1", "file": name, "at": 0.0, "in": 0.0, "out": 1.0, "gain": 0.2, "fade_in": 0.0, "fade_out": 0.0},
    ]}
    store.save_project(record)
    return pid


def _expected_peaks() -> dict:
    return waveform.peaks_payload(waveform.peaks_from_samples(SIGNAL, RATE), 2.5, RATE)


def _leftovers() -> list[str]:
    return sorted(p.name for p in music.MUSIC_DIR.glob("*.part")) if music.MUSIC_DIR.exists() else []


# ── the index and the files ───────────────────────────────────────────────────

def test_the_library_starts_empty_and_the_index_is_written_atomically_under_a_lock(decodes):
    assert music.list_files() == [] and music.library() == {}, "no directory yet is an empty library"
    assert not music.MUSIC_DIR.exists(), "listing creates nothing"
    assert isinstance(music._lock, type(threading.Lock()))

    entry = music.add_file("bed.mp3", b"mp3-bytes", "Olive Owner")
    held = json.loads((music.MUSIC_DIR / music.INDEX_NAME).read_text(encoding="utf-8"))
    assert held == {"files": {"bed.mp3": {k: v for k, v in entry.items() if k != "name"}}}
    assert _leftovers() == [], "every .part was published or removed"


def test_add_file_stores_the_file_its_peaks_and_its_index_entry(decodes):
    entry = music.add_file("bed.mp3", b"mp3-bytes", "Olive Owner")
    assert set(entry) == {"name", "size", "duration", "sample_rate", "channels", "uploaded_at", "uploaded_by"}
    assert (entry["name"], entry["size"], entry["duration"], entry["sample_rate"], entry["channels"], entry["uploaded_by"]) == (
        "bed.mp3", 9, 2.5, RATE, 2, "Olive Owner",
    )
    assert entry["uploaded_at"].endswith("+00:00")

    # Decoded once, from the part - never from the published file, and never twice.
    assert decodes == [music.MUSIC_DIR / "bed.mp3.part"]
    assert (music.MUSIC_DIR / "bed.mp3").read_bytes() == b"mp3-bytes"

    # The peaks: the waveform strip's own arithmetic over the decoded samples,
    # in the waveform route's shape, cached beside the file.
    cached = json.loads((music.MUSIC_DIR / "bed.mp3.peaks.json").read_text(encoding="utf-8"))
    assert cached == _expected_peaks() == music.peaks("bed.mp3")
    assert set(cached) == {"buckets", "bucket_seconds", "duration", "sample_rate", "peaks"}
    assert cached["buckets"] == 800 == len(cached["peaks"]), "2.5 s is drawn at the floor, as the strip draws it"
    assert cached["duration"] == 2.5 and cached["sample_rate"] == RATE

    assert music.list_files() == [entry]
    assert music.library() == {"bed.mp3": 2.5} and music.duration_of("bed.mp3") == 2.5
    assert music.duration_of("other.mp3") is None
    assert music.get_path("bed.mp3") == (music.MUSIC_DIR / "bed.mp3").resolve()


def test_the_list_is_sorted_by_name(decodes):
    for name in ("Zebra.mp3", "alpha.wav", "Mid.flac"):
        music.add_file(name, b"x", "u")
    assert [e["name"] for e in music.list_files()] == ["Mid.flac", "Zebra.mp3", "alpha.wav"]


def test_the_name_is_sanitised_once_and_the_extension_lower_cased(decodes):
    """``utils.helpers.sanitize_filename``, exactly as the project importer
    sanitises: the characters Windows refuses become ``_``. The stored name
    is what every clip will refer to."""
    entry = music.add_file(" My Song/Take:2.MP3 ", b"x", "u")
    assert entry["name"] == "My Song_Take_2.mp3"
    assert (music.MUSIC_DIR / "My Song_Take_2.mp3").is_file()
    assert music.checked_name("bed.FLAC") == "bed.flac"


@pytest.mark.parametrize("raw, message", [
    ("", "needs a name"),
    (".mp3", "not a music file"),      # splitext sees no extension in a bare one
    ("   .mp3", "not a music file"),
    ("..mp3", "not a music file"),
    (".hidden.mp3", "may not start with a dot"),
    ("notes.txt", "not a music file"),
    ("bed", "not a music file"),
    ("bed.mp3.", "not a music file"),
    ("a\nb.mp3", "control character"),
    ("a\x00b.mp3", "control character"),
    ("x" * 117 + ".mp3", "limited to 120 characters"),
])
def test_a_name_the_library_cannot_store_is_refused(decodes, raw, message):
    with pytest.raises(music.BadName) as exc:
        music.add_file(raw, b"x", "u")
    assert message in str(exc.value), str(exc.value)
    assert not music.MUSIC_DIR.exists() or list(music.MUSIC_DIR.iterdir()) == []


def test_the_whitelist_is_the_six_extensions_case_insensitively(decodes):
    for ext in music.ALLOWED_EXTENSIONS:
        assert music.checked_name(f"bed{ext.upper()}") == f"bed{ext}"
    assert music.ALLOWED_EXTENSIONS == (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


def test_an_existing_name_is_refused_and_never_replaced(decodes):
    """A clip refers to a file by NAME, so a silent replacement would change
    every project that uses it: 409, rename or delete the old one. Matched
    with ``casefold``, so the index can never hold two names Windows cannot
    tell apart - and Linux behaves the same way."""
    music.add_file("bed.mp3", b"first", "u")
    with pytest.raises(music.MusicExists) as exc:
        music.add_file("BED.mp3", b"second", "u")
    assert "already in the library" in str(exc.value)
    assert (music.MUSIC_DIR / "bed.mp3").read_bytes() == b"first"
    assert len(decodes) == 1, "refused before any decode"

    # A file on disk with no index row (an interrupted upload) reserves its
    # name too, and so does an upload in flight (its .part) - each of them
    # case-insensitively.
    (music.MUSIC_DIR / "orphan.mp3").write_bytes(b"orphan")
    with pytest.raises(music.MusicExists):
        music.add_file("orphan.mp3", b"x", "u")
    with pytest.raises(music.MusicExists):
        music.add_file("Orphan.MP3", b"x", "u")
    (music.MUSIC_DIR / "inflight.mp3.part").write_bytes(b"...")
    with pytest.raises(music.MusicExists):
        music.add_file("inflight.mp3", b"x", "u")
    with pytest.raises(music.MusicExists):
        music.add_file("INFLIGHT.mp3", b"x", "u")
    assert len(decodes) == 1, "every one refused before any decode"
    # The peaks file beside a name reserves nothing of its own.
    assert music.add_file("bed.mp3.peaks.json.mp3", b"x", "u")["name"] == "bed.mp3.peaks.json.mp3"


def test_a_file_on_disk_without_a_row_is_removable_only_under_its_exact_name(decodes):
    """The hatch for an interrupted upload - and for an index that could not
    be read - stays, so a name never gets stuck; but it fires on the ON-DISK
    spelling, not on ``is_file()``, which on Windows answers for a name the
    library never held."""
    music.MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    (music.MUSIC_DIR / "orphan.mp3").write_bytes(b"orphan")

    with pytest.raises(music.MusicNotFound):
        music.remove("ORPHAN.mp3")
    assert (music.MUSIC_DIR / "orphan.mp3").read_bytes() == b"orphan", "the wrong case removed nothing"

    music.remove("orphan.mp3")
    assert not (music.MUSIC_DIR / "orphan.mp3").exists()
    assert music.add_file("orphan.mp3", b"x", "u")["name"] == "orphan.mp3", "and the name is free again"


def test_ten_uploads_at_once_into_a_library_that_is_not_there_yet_all_succeed(decodes):
    """Two things at once, both found by the Reviewer. ``_path_for`` resolved
    the directory twice while it was being created and Windows answered the
    second in its ``\\\\?\\`` form, so a good name was "no such file" (108 of
    150 concurrent first uploads) and the upload route answered 500; and the
    index's read-modify-write has to be serialised, or eight of ten uploads
    lose their row."""
    assert not music.MUSIC_DIR.exists(), "the first uploads on a fresh install"
    names = [f"n{i}.mp3" for i in range(10)]
    errors: list[str] = []

    def upload(name: str) -> None:
        try:
            music.add_file(name, b"mp3-bytes", "u")
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=upload, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert [entry["name"] for entry in music.list_files()] == sorted(names), "ten rows, not two"
    assert sorted(p.name for p in music.MUSIC_DIR.glob("*.mp3")) == sorted(names)
    assert _leftovers() == []


def test_the_file_the_peaks_and_the_index_are_each_published_from_a_part(decodes, monkeypatch):
    """Trap 10 for all three writes, in the order they happen: nothing is
    written in place, so nothing can leave a half-written index behind a
    complete file."""
    published: list[tuple[str, str]] = []
    real = music.replace_with_retry
    monkeypatch.setattr(music, "replace_with_retry",
                        lambda src, dst: (published.append((Path(src).name, Path(dst).name)), real(src, dst))[1])

    music.add_file("two.mp3", b"mp3-bytes", "u")
    assert published == [
        ("two.mp3.peaks.json.part", "two.mp3.peaks.json"),
        ("two.mp3.part", "two.mp3"),
        ("index.json.part", "index.json"),
    ]


def test_the_size_cap_is_refused_before_anything_is_written(decodes, monkeypatch):
    monkeypatch.setattr(music, "MAX_MUSIC_BYTES", 8)
    with pytest.raises(music.TooLarge) as exc:
        music.add_file("bed.mp3", b"123456789", "u")
    assert "limited to" in str(exc.value)
    assert decodes == [] and not music.MUSIC_DIR.exists()
    assert music.add_file("bed.mp3", b"12345678", "u")["size"] == 8


def test_a_file_that_does_not_decode_is_refused_and_leaves_nothing_behind(decodes):
    with pytest.raises(music.NotAudio):
        music.add_file("noise.mp3", NOISE + b"garbage", "u")
    assert decodes == [music.MUSIC_DIR / "noise.mp3.part"], "it was decoded, once, to find out"
    assert sorted(p.name for p in music.MUSIC_DIR.iterdir()) == [], "no part, no file, no peaks, no index"
    assert music.list_files() == []
    # And the name is free.
    assert music.add_file("noise.mp3", b"real", "u")["name"] == "noise.mp3"


def test_an_unreadable_index_reads_as_empty_rather_than_failing_every_route(decodes):
    music.add_file("bed.mp3", b"x", "u")
    (music.MUSIC_DIR / music.INDEX_NAME).write_text("{ not json", encoding="utf-8")
    assert music.list_files() == []
    with pytest.raises(music.MusicExists):
        music.add_file("bed.mp3", b"x", "u")  # the file on disk still holds its name
    music.remove("bed.mp3")  # ... and can be cleared
    assert not (music.MUSIC_DIR / "bed.mp3").exists()


def test_the_peaks_are_recomputed_once_when_the_cache_has_gone(decodes):
    music.add_file("bed.mp3", b"x", "u")
    (music.MUSIC_DIR / "bed.mp3.peaks.json").unlink()
    assert music.peaks("bed.mp3") == _expected_peaks()
    assert decodes == [music.MUSIC_DIR / "bed.mp3.part", music.MUSIC_DIR / "bed.mp3"]
    assert (music.MUSIC_DIR / "bed.mp3.peaks.json").is_file(), "re-cached"
    music.peaks("bed.mp3")
    assert len(decodes) == 2, "served from the cache again"
    with pytest.raises(music.MusicNotFound):
        music.peaks("other.mp3")


# ── the reference check ───────────────────────────────────────────────────────

def test_remove_is_refused_with_the_projects_that_use_the_file_then_allowed(decodes):
    music.add_file("bed.mp3", b"x", "u")
    music.add_file("other.mp3", b"y", "u")
    alpha = _project_using("bed.mp3", "Alpha")
    beta = _project_using("bed.mp3", "Beta")
    _project_using("other.mp3", "Gamma")
    # Records that cannot name a clip name nothing: no edit, an edit with no
    # music, a music key that is not a list, a clip that is not a dict.
    store.import_upload("plain.mp4", b"v")
    _project_using("bed.mp3", "Odd", clips=["bed.mp3", {"file": None}])
    other = store.import_upload("noise.mp4", b"v")
    record = store.get_project(other["id"])
    record["edit"] = {"version": 2, "music": "bed.mp3"}
    store.save_project(record)

    assert sorted(music.references("bed.mp3")) == ["Alpha", "Beta"]
    assert music.references("other.mp3") == ["Gamma"]
    assert music.references("nobody.mp3") == []

    with pytest.raises(music.MusicInUse) as exc:
        music.remove("bed.mp3")
    assert sorted(exc.value.projects) == ["Alpha", "Beta"] and exc.value.name == "bed.mp3"
    assert "2 projects" in str(exc.value) and "Alpha" in str(exc.value) and "Beta" in str(exc.value)
    assert (music.MUSIC_DIR / "bed.mp3").is_file(), "nothing removed"

    for pid in (alpha, beta):
        record = store.get_project(pid)
        record.pop("edit")
        store.save_project(record)
    music.remove("bed.mp3")
    assert not (music.MUSIC_DIR / "bed.mp3").exists()
    assert not (music.MUSIC_DIR / "bed.mp3.peaks.json").exists()
    assert [e["name"] for e in music.list_files()] == ["other.mp3"]
    with pytest.raises(music.MusicNotFound):
        music.remove("bed.mp3")
    with pytest.raises(music.MusicNotFound):
        music.get_path("bed.mp3")


def test_a_crafted_name_never_resolves_outside_the_library(decodes, tmp_path):
    """The service resolves the path and confirms it sits directly inside
    ``MUSIC_DIR`` before it is read, unlinked or written."""
    music.add_file("bed.mp3", b"x", "u")
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"decoy")
    for name in ("..", "../outside.mp3", "..\\outside.mp3", "/outside.mp3", "a/b.mp3", ".hidden.mp3",
                 "x.txt", "a\x00b.mp3", "", "bed.mp3/../../outside.mp3"):
        with pytest.raises(music.MusicNotFound):
            music.get_path(name)
        with pytest.raises(music.MusicNotFound):
            music.remove(name)
        with pytest.raises(music.MusicNotFound):
            music.peaks(name)
    assert outside.read_bytes() == b"decoy"
    assert music.valid_name("bed.mp3") and not music.valid_name("../bed.mp3") and not music.valid_name(None)


def test_the_media_type_follows_the_extension():
    assert music.media_type_of("bed.mp3") == "audio/mpeg"
    assert music.media_type_of("bed.WAV") == "audio/wav"
    assert music.media_type_of("bed.m4a") == "audio/mp4"
    assert music.media_type_of("bed.aac") == "audio/aac"
    assert music.media_type_of("bed.ogg") == "audio/ogg"
    assert music.media_type_of("bed.flac") == "audio/flac"
    assert music.media_type_of("bed.xyz") == "application/octet-stream"


# ── the routes ────────────────────────────────────────────────────────────────

def test_every_route_needs_a_session(client, decodes):
    from api.app import app

    music.add_file("bed.mp3", b"x", "u")
    anonymous = TestClient(app)
    assert anonymous.get("/api/music").status_code == 401
    assert anonymous.post("/api/music", files={"file": ("a.mp3", b"x", "audio/mpeg")}).status_code == 401
    assert anonymous.get("/api/music/bed.mp3").status_code == 401
    assert anonymous.get("/api/music/bed.mp3/peaks").status_code == 401
    assert anonymous.delete("/api/music/bed.mp3").status_code == 401
    assert (music.MUSIC_DIR / "bed.mp3").is_file()


def test_upload_list_serve_peaks_and_delete_through_the_routes(client, decodes):
    r = _upload(client, "Bed Track.mp3", b"mp3-bytes")
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["name"] == "Bed Track.mp3" and entry["size"] == 9 and entry["duration"] == 2.5
    assert entry["uploaded_by"] == "Administrator"
    assert _upload(client, "alpha.wav", b"wav-bytes").status_code == 200

    rows = _rows("music.upload")
    assert [(row["entity"], row["entity_id"], row["detail"]) for row in rows] == [
        ("music", "alpha.wav", "alpha.wav (9 bytes)"),
        ("music", "Bed Track.mp3", "Bed Track.mp3 (9 bytes)"),
    ], "name and size, no more"

    r = client.get("/api/music")
    assert r.status_code == 200
    assert [f["name"] for f in r.json()["files"]] == ["Bed Track.mp3", "alpha.wav"]
    assert r.json()["files"][0] == entry

    # The file, inline, typed by its extension, cacheable for a day: the name
    # is immutable, so unlike the re-voiced outputs it is not ``no-cache``.
    r = client.get("/api/music/Bed%20Track.mp3")
    assert r.status_code == 200
    assert r.content == b"mp3-bytes"
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["cache-control"] == "private, max-age=86400"
    r = client.get("/api/music/alpha.wav")
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/wav")

    r = client.get("/api/music/Bed%20Track.mp3/peaks")
    assert r.status_code == 200 and r.json() == _expected_peaks()

    r = client.delete("/api/music/Bed%20Track.mp3")
    assert r.status_code == 204, r.text
    assert [(row["entity_id"], row["detail"]) for row in _rows("music.delete")] == [("Bed Track.mp3", "Bed Track.mp3")]
    assert client.get("/api/music/Bed%20Track.mp3").status_code == 404
    assert client.get("/api/music/Bed%20Track.mp3/peaks").status_code == 404
    assert client.delete("/api/music/Bed%20Track.mp3").status_code == 404
    assert [f["name"] for f in client.get("/api/music").json()["files"]] == ["alpha.wav"]


def test_the_upload_answers_409_413_and_400(client, decodes, monkeypatch):
    assert _upload(client, "bed.mp3").status_code == 200
    r = _upload(client, "bed.mp3", b"other")
    assert r.status_code == 409 and "rename the file or delete the old one" in r.json()["detail"]
    assert (music.MUSIC_DIR / "bed.mp3").read_bytes() == b"mp3-bytes", "never replaced"

    r = _upload(client, "noise.mp3", NOISE + b"garbage")
    assert r.status_code == 400 and r.json()["detail"] == "faked: noise.mp3 is not audio", (
        "the refusal names the UPLOAD, not the .part it was decoded from"
    )
    r = _upload(client, "notes.txt", b"words")
    assert r.status_code == 400 and "not a music file" in r.json()["detail"]
    r = _upload(client, ".hidden.mp3", b"x")
    assert r.status_code == 400 and "start with a dot" in r.json()["detail"]
    r = _upload(client, "empty.mp3", b"")
    assert r.status_code == 400 and "empty" in r.json()["detail"]

    monkeypatch.setattr(music, "MAX_MUSIC_BYTES", 16)
    r = _upload(client, "big.mp3", b"x" * 17)
    assert r.status_code == 413 and "limited to" in r.json()["detail"]
    assert _upload(client, "fits.mp3", b"x" * 16).status_code == 200

    assert [f["name"] for f in music.list_files()] == ["bed.mp3", "fits.mp3"]
    assert _leftovers() == []
    assert [row["entity_id"] for row in _rows("music.upload")] == ["fits.mp3", "bed.mp3"], "no row for a refusal"


def test_delete_is_refused_with_the_projects_that_use_the_file(client, decodes):
    assert _upload(client, "bed.mp3").status_code == 200
    _project_using("bed.mp3", "Corpus")
    r = client.delete("/api/music/bed.mp3")
    assert r.status_code == 409, r.text
    assert "Corpus" in r.json()["detail"] and "remove its clips" in r.json()["detail"]
    assert (music.MUSIC_DIR / "bed.mp3").is_file()
    assert _rows("music.delete") == [], "a refusal records nothing"


def test_a_delete_with_a_differently_cased_name_touches_nothing(client, decodes):
    """The one that got through: Windows answers ``is_file()`` for
    ``BED.mp3`` when ``bed.mp3`` is what is there, so the "a file with no
    row is removable" hatch passed, ``references('BED.mp3')`` found nothing
    (every clip says ``bed.mp3``), the file a project was using was unlinked
    BY PATH and its index row survived - a 204, a deleted file and a phantom
    row. Every lookup is exact now."""
    assert _upload(client, "bed.mp3").status_code == 200
    _project_using("bed.mp3", "Corpus")

    r = client.delete("/api/music/BED.mp3")
    assert r.status_code == 404, r.text
    assert (music.MUSIC_DIR / "bed.mp3").read_bytes() == b"mp3-bytes", "the referenced file is still there"
    assert (music.MUSIC_DIR / "bed.mp3.peaks.json").is_file()
    assert [f["name"] for f in client.get("/api/music").json()["files"]] == ["bed.mp3"], "and its row"
    assert _rows("music.delete") == [], "nothing removed, nothing audited"

    # The exact name is the one the guard answers for, with the projects.
    r = client.delete("/api/music/bed.mp3")
    assert r.status_code == 409 and "Corpus" in r.json()["detail"]

    # Every other lookup of the wrong case is a 404, never the other file.
    assert client.get("/api/music/BED.mp3").status_code == 404
    assert client.get("/api/music/BED.mp3/peaks").status_code == 404
    assert client.get("/api/music/bed.mp3").content == b"mp3-bytes"
    with pytest.raises(music.MusicNotFound):
        music.get_path("BED.mp3")

    # ... and an upload that would create the collision is refused.
    r = _upload(client, "BED.mp3", b"second")
    assert r.status_code == 409 and "rename the file" in r.json()["detail"]
    assert (music.MUSIC_DIR / "bed.mp3").read_bytes() == b"mp3-bytes"
    assert [f["name"] for f in music.list_files()] == ["bed.mp3"]
    assert music.duration_of("bed.mp3") == 2.5 and music.duration_of("BED.mp3") is None, "the index is keyed exactly"


def test_a_name_that_will_not_resolve_is_a_400_from_the_upload_not_a_500(client, decodes, monkeypatch):
    """Belt to the resolution race: ``_path_for`` answers ``MusicNotFound``
    for a name it cannot place inside the library, and the upload route
    caught four exception types, not that one."""
    def refuse(*args, **kwargs):
        raise music.MusicNotFound("No music file named 'bed.mp3'.")

    monkeypatch.setattr(music, "add_file", refuse)
    r = _upload(client, "bed.mp3")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "No music file named 'bed.mp3'."


def test_a_crafted_name_never_reaches_a_file_outside_the_library_through_the_routes(client, decodes, tmp_path):
    """The path parameter's pattern refuses a backslash and a control
    character (422); a separator never reaches the music router at all -
    the decoded path matches no API route, so a DELETE is a 405 and a GET
    falls through to the SPA catch-all like any unknown page. Either way
    the decoy outside the directory is never served."""
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"decoy")
    assert _upload(client, "bed.mp3").status_code == 200

    for name in ("a%5Cb.mp3", "a%00b.mp3", "a%0Ab.mp3", "a%7Fb.mp3"):
        for method, suffix in (("GET", ""), ("GET", "/peaks"), ("DELETE", "")):
            r = client.request(method, f"/api/music/{name}{suffix}")
            assert r.status_code == 422, (method, name, r.status_code)
            assert r.json()["detail"][0]["type"] == "string_pattern_mismatch"

    for name in ("..", "..%2F..%2Foutside.mp3", "a%2Fb.mp3", "bed.mp3%2F..%2F..%2Foutside.mp3"):
        for method, suffix in (("GET", ""), ("GET", "/peaks"), ("DELETE", "")):
            r = client.request(method, f"/api/music/{name}{suffix}")
            assert r.content != b"decoy", (method, name)
            assert not r.headers.get("content-type", "").startswith("audio/"), (method, name)
            assert r.status_code in (404, 405, 422) or r.headers["content-type"].startswith("text/html"), (method, name, r.status_code)

    for name in (".hidden.mp3", "notes.txt", "missing.mp3"):
        assert client.get(f"/api/music/{name}").status_code == 404
        assert client.delete(f"/api/music/{name}").status_code == 404
    assert outside.read_bytes() == b"decoy" and (music.MUSIC_DIR / "bed.mp3").is_file()


def test_the_ownership_sweep_lists_the_library_routes_as_studio_wide():
    """The routes carry no project id on purpose (spec §12.3), so they are in
    the sweep's exclusion list with the reason rather than in its table."""
    from test_project_ownership import PROJECT_SCOPED_ROUTES, STUDIO_WIDE_ROUTES

    for route in (("GET", "/api/music"), ("POST", "/api/music"), ("GET", "/api/music/{name}"),
                  ("GET", "/api/music/{name}/peaks"), ("DELETE", "/api/music/{name}")):
        assert route in STUDIO_WIDE_ROUTES, route
        assert route not in PROJECT_SCOPED_ROUTES


# ── what a refusal says ───────────────────────────────────────────────────────

def test_the_decode_refusal_keeps_the_reason_and_drops_every_path():
    """The message reaches the browser. ffmpeg's ``[png @ 0x…]`` prefix is
    noise, the scratch WAV and the ``.part`` are not the file anyone
    uploaded, and a server path is nobody's business - so the first
    path-free line is the whole of the detail, and a stderr with nothing
    path-free to say leaves no detail at all rather than a leak."""
    live = ("[png @ 0000000002868c40] chunk too big\n"
            "[out#0/wav @ 0000000002869a80] Output file does not contain any stream\n"
            "Error opening output file C:\\Projects\\Media-Studio-Enterprise\\data\\temp\\"
            "music-decode-qpee9xep\\decoded.wav.\n"
            "Error opening output files: Invalid argument\n")
    assert music._decode_reason(live, "C:\\x\\fake.mp3.part") == "chunk too big"
    assert music._decode_reason("[mp3 @ 0x1] Header missing\n") == "Header missing"
    assert music._decode_reason("C:\\lib\\bed.mp3: Invalid data found when processing input\n") == ""
    assert music._decode_reason("/srv/media/bed.mp3: Invalid data found\n") == ""
    assert music._decode_reason("[mp3 @ 0x1] C:\\lib\\x.mp3 is bad\nnot seekable\n") == "not seekable"
    assert music._decode_reason("only the part is named\n", "only the part is named") == ""
    assert music._decode_reason("") == "" and music._decode_reason(None) == ""


# ── the real decode ───────────────────────────────────────────────────────────

def _real_ffmpeg() -> str | None:
    path = config_module.FFMPEG_PATH
    return path if path and Path(path).is_file() else None


def _stereo_wav(path: Path, rate: int = 8000, seconds: float = 2.0) -> np.ndarray:
    """Two seconds of stereo: a tone on the left, and on the right a louder
    NEGATIVE burst every 100 frames, so the mix-down rule (the louder channel
    per frame, sign kept) is what decides the peaks."""
    frames = int(rate * seconds)
    t = np.arange(frames) / rate
    left = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    right = np.zeros(frames, dtype=np.int16)
    right[::100] = -30000
    interleaved = np.empty(frames * 2, dtype="<i2")
    interleaved[0::2] = left
    interleaved[1::2] = right
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(interleaved.tobytes())
    return interleaved


def test_a_real_file_decodes_through_ffmpeg_alone_and_agrees_with_the_strip(tmp_path, monkeypatch):
    """The seam runs the resolved ffmpeg - never ffprobe, which the packaged
    app does not have - and the peaks it gives a stereo file are exactly what
    ``waveform.compute_peaks`` draws for the same file."""
    ffmpeg = _real_ffmpeg()
    if not ffmpeg:
        pytest.skip("no ffmpeg on this machine")
    ran: list[list[str]] = []
    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (ran.append(list(cmd)), real_run(cmd, **kw))[1])
    source = tmp_path / "in" / "tone.wav"
    _stereo_wav(source)

    decoded = music.decode_audio(source)
    assert (decoded.duration, decoded.sample_rate, decoded.channels) == (2.0, 8000, 2)
    assert decoded.samples.dtype == np.int16 and decoded.samples.shape == (16000,)
    assert decoded.samples[0] == -30000 and decoded.samples[100] == -30000, "the louder channel, sign kept"
    assert decoded.samples[50] != 0, "the tone where the burst is silent"

    (cmd,) = ran
    assert cmd[0] == ffmpeg and "-i" in cmd and cmd[cmd.index("-i") + 1] == str(source)
    assert not any("ffprobe" in str(arg).lower() for arg in cmd)
    assert cmd[cmd.index("-c:a") + 1] == "pcm_s16le" and "-vn" in cmd

    strip = waveform.compute_peaks(source)
    mine = waveform.peaks_from_samples(decoded.samples, decoded.sample_rate)
    assert mine == strip["peaks"] and len(mine) == strip["buckets"] == 800
    assert max(mine) == 233, "30000/32768 of full scale, from the negative channel"
    assert not list((tmp_path / "temp").glob("music-decode-*")), "the scratch WAV is removed"


def test_a_file_that_is_not_audio_is_refused_by_the_real_decoder(tmp_path):
    if not _real_ffmpeg():
        pytest.skip("no ffmpeg on this machine")
    junk = tmp_path / "junk.mp3"
    junk.write_bytes(b"this is not an audio file at all, whatever its name says " * 20)
    with pytest.raises(music.NotAudio) as exc:
        music.decode_audio(junk)
    assert "not an audio file this app can decode" in str(exc.value)
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")
    with pytest.raises(music.NotAudio):
        music.decode_audio(empty)


def test_a_png_named_mp3_is_refused_by_the_uploads_name_and_leaks_no_path(client, tmp_path):
    """End to end on the real binary: the 400 names ``fake.mp3`` - the file
    the user chose - with ffmpeg's own first reason and nothing of the
    server's scratch directory in it."""
    ffmpeg = _real_ffmpeg()
    if not ffmpeg:
        pytest.skip("no ffmpeg on this machine")
    png = tmp_path / "card.png"
    made = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "color=c=red:s=32x32", "-frames:v", "1", str(png)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    if made.returncode != 0 or not png.is_file():
        pytest.skip(f"this ffmpeg cannot write a png: {made.stderr[-200:]}")

    r = _upload(client, "fake.mp3", png.read_bytes())
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail.startswith("'fake.mp3' is not an audio file this app can decode") and detail.endswith(".")
    for leak in (".part", "decoded.wav", "music-decode", str(tmp_path), str(config_module.TEMP_DIR), ":\\", ":/", "@"):
        assert leak not in detail, (leak, detail)
    assert music.list_files() == [] and _leftovers() == []


def test_a_real_mp3_goes_through_the_upload_route_end_to_end(client, tmp_path):
    """The whole POST with a real compressed file: encoded here by ffmpeg,
    decoded by the seam, measured, its peaks cached, served back typed."""
    ffmpeg = _real_ffmpeg()
    if not ffmpeg:
        pytest.skip("no ffmpeg on this machine")
    mp3 = tmp_path / "tone.mp3"
    encoded = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=22050:duration=1.5", "-af", "volume=8", "-ac", "2",  # sine's default is 0.1 of full scale
         "-c:a", "libmp3lame", str(mp3)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    if encoded.returncode != 0 or not mp3.is_file():
        pytest.skip(f"this ffmpeg cannot encode mp3: {encoded.stderr[-200:]}")

    r = _upload(client, "tone.mp3", mp3.read_bytes())
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["sample_rate"] == 22050 and entry["channels"] == 2
    assert 1.4 <= entry["duration"] <= 1.7, "an MP3 frame pads the tail; the decode's own length is what is recorded"
    peaks = client.get("/api/music/tone.mp3/peaks").json()
    assert peaks["buckets"] == 800 == len(peaks["peaks"]) and max(peaks["peaks"]) > 150
    got = client.get("/api/music/tone.mp3")
    assert got.status_code == 200 and got.content == mp3.read_bytes()
    assert _leftovers() == []
