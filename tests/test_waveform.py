"""The waveform strip's peaks (narration timeline, phase 3a).

``GET /api/projects/{pid}/waveform`` reduces the project's own ``audio.wav`` -
the 16 kHz mono file the transcribe step already extracts - to one magnitude per
125 ms bucket, which is what the timeline draws its strip from.

Four decisions are pinned here rather than left to a code reading:

- **no ffmpeg and no ffprobe.** ``audio.wav`` is OUR file, written as PCM by the
  importer, so the stdlib ``wave`` module reads it directly. ffprobe may not
  exist at all in the packaged app (the imageio fallback ships ffmpeg only), and
  the length the strip is drawn against must come from the WAV header - the
  scale of the file actually being drawn.
- **the resolution is fixed, not a query parameter.** 8 buckets a second between
  a floor of 800 and a ceiling of 12000, so there is exactly ONE cache entry per
  project however wide the strip is drawn; the client max-pools down to its own
  pixel width, which is exact and cheap.
- **the file is read in chunks and no bucket straddles a read.** A two-hour
  recording is 115 MB of PCM and must never be loaded whole to produce ~58 kB of
  JSON, and a chunk boundary that split a bucket would put a notch in the
  drawing. The chunk size is squeezed here so the loop is genuinely exercised.
- **the cache is keyed on the source's mtime AND size.** Re-transcribing rewrites
  ``audio.wav``; nothing has to remember to invalidate anything.

Sample rates below are as low as 100 Hz on purpose: ``duration`` is
``nframes / framerate``, so a 1600-second recording is 320 kB rather than 50 MB
and the ceiling can be tested against a real file instead of only against the
arithmetic.
"""

import json
import os
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import projects as store
from services import waveform
from utils.config import config


def _wav(path, samples, *, rate=16000, width=2, channels=1):
    """Write ``samples`` (already interleaved) as a PCM WAV file."""
    dtype = {1: np.uint8, 2: "<i2", 4: "<i4"}[width]
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(np.asarray(samples, dtype=dtype).tobytes())
    return path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(store, "_locks", {})


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _video(with_audio=True, **wav_kwargs) -> str:
    """A video project, by default with the audio a transcribe would have left."""
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]
    store.set_transcript(pid, [{"start": 0.0, "end": 2.0, "text": "First sentence."}])
    if with_audio:
        rate = wav_kwargs.pop("rate", 1000)
        frames = wav_kwargs.pop("frames", 8000)  # 8 s at 1000 Hz
        _wav(waveform.audio_path(pid), np.zeros(frames, dtype="<i2"), rate=rate, **wav_kwargs)
    return pid


# ── the payload ──────────────────────────────────────────────────────────────

def test_the_payload_is_one_magnitude_per_bucket_with_the_scale_it_was_drawn_at(client):
    """Five keys and nothing else: the client needs the length to place the
    sentence blocks, the bucket size to line them up with the strip, and the
    peaks themselves. No min/max PAIR - mono speech drawn as a symmetric strip
    gains nothing from a second value and the payload doubles."""
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]
    store.set_transcript(pid, [{"start": 0.0, "end": 2.0, "text": "A sentence."}])
    # 341.0 s, which is the real case the module was measured on.
    _wav(waveform.audio_path(pid), np.zeros(341_000, dtype="<i2"), rate=1000)

    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert set(payload) == {"buckets", "bucket_seconds", "duration", "sample_rate", "peaks"}
    assert payload["duration"] == pytest.approx(341.0)
    assert payload["sample_rate"] == 1000
    assert payload["buckets"] == 2728 == len(payload["peaks"])
    assert payload["bucket_seconds"] == pytest.approx(0.125), "8 buckets a second, fixed"
    assert all(isinstance(p, int) and 0 <= p <= 255 for p in payload["peaks"])


def test_a_peak_is_the_loudest_sample_in_its_bucket_scaled_to_a_byte(client):
    """Loudest, not average: a strip drawn from averages flattens speech into a
    uniform smear and the bursts the user is aiming a sentence at disappear."""
    pid = _video(with_audio=False)
    # 800 s at 1 Hz -> 6400 buckets, one sample each. Silence, then half scale,
    # then full scale, so the scaling can be read off the answer directly.
    samples = np.zeros(800, dtype="<i2")
    samples[100] = 16384        # half of full scale
    samples[200] = 32767        # full scale
    samples[300] = -32768       # ... and the same magnitude, negative
    _wav(waveform.audio_path(pid), samples, rate=1)

    peaks = client.get(f"/api/projects/{pid}/waveform").json()["peaks"]
    assert len(peaks) == 800, "one bucket per sample here"
    assert peaks[0] == 0
    assert peaks[100] == 127
    assert peaks[200] >= 254
    assert peaks[300] == 255, "the sign is thrown away; only the magnitude is drawn"


# ── the resolution: 8 a second between a floor and a ceiling ─────────────────

def test_the_bucket_count_is_eight_a_second_between_the_floor_and_the_ceiling():
    """The arithmetic on its own, including the two clamps, so the file-backed
    tests below need only confirm that the module uses it."""
    assert waveform.bucket_count(341.01) == 2728
    assert waveform.bucket_count(100.0) == 800, "the floor and the rate meet exactly here"
    assert waveform.bucket_count(1500.0) == 12000, "... and so do the ceiling and the rate"
    assert waveform.bucket_count(0.5) == waveform.MIN_BUCKETS
    assert waveform.bucket_count(7200.0) == waveform.MAX_BUCKETS


def test_a_short_clip_is_drawn_at_the_floor_rather_than_as_a_handful_of_bars(client):
    """10 s would be 80 buckets at the fixed rate, which is a strip you cannot
    aim a sentence at. The floor gives it 800."""
    pid = _video(with_audio=False)
    _wav(waveform.audio_path(pid), np.zeros(10_000, dtype="<i2"), rate=1000)

    payload = client.get(f"/api/projects/{pid}/waveform").json()
    assert payload["duration"] == pytest.approx(10.0)
    assert payload["buckets"] == 800 == len(payload["peaks"])
    assert payload["bucket_seconds"] == pytest.approx(0.0125)


def test_a_long_recording_is_capped_so_the_payload_stays_small(client):
    """1600 s would be 12800 buckets; the ceiling holds it at 12000, about 58 kB
    of JSON. Past the cap a bucket is simply wider than 125 ms."""
    pid = _video(with_audio=False)
    _wav(waveform.audio_path(pid), np.zeros(160_000, dtype="<i2"), rate=100)

    payload = client.get(f"/api/projects/{pid}/waveform").json()
    assert payload["duration"] == pytest.approx(1600.0)
    assert payload["buckets"] == 12000 == len(payload["peaks"])
    assert payload["bucket_seconds"] == pytest.approx(1600.0 / 12000)
    assert len(json.dumps(payload)) < 80_000


def test_a_clip_with_fewer_frames_than_the_floor_is_not_asked_for_empty_buckets():
    """A fixture, not a recording, but it must not crash: an empty bucket has no
    maximum and ``reduceat`` on a repeated index does not mean what it looks
    like it means."""
    pid = _video(with_audio=False)
    _wav(waveform.audio_path(pid), np.arange(50, dtype="<i2") * 100, rate=10)

    payload = waveform.compute_peaks(waveform.audio_path(pid))
    assert payload["buckets"] == 50 == len(payload["peaks"])


# ── the chunked read ─────────────────────────────────────────────────────────

def test_reading_in_chunks_gives_the_same_peaks_as_reading_it_whole(monkeypatch):
    """The two-hour case is why the read is chunked at all, and the loop that
    keeps whole buckets inside one read is the fiddly part of this module. The
    chunk size is squeezed to a few hundred frames so a 4000-frame file makes it
    go round many times, and the answer must not move."""
    pid = _video(with_audio=False)
    rng = np.random.default_rng(7)
    samples = rng.integers(-32768, 32767, size=4000, dtype=np.int64).astype("<i2")
    path = _wav(waveform.audio_path(pid), samples, rate=10)

    whole = waveform.compute_peaks(path)
    monkeypatch.setattr(waveform, "CHUNK_FRAMES", 137)
    chunked = waveform.compute_peaks(path)
    assert chunked == whole

    # And the peaks really are this file's, not a smooth approximation of it.
    edges = np.linspace(0, 4000, whole["buckets"] + 1).astype(int)
    expected = [
        int(min(255, np.abs(samples[a:b].astype(np.int32)).max() * 255.0 / 32768.0))
        for a, b in zip(edges[:-1], edges[1:])
    ]
    assert whole["peaks"] == expected


def test_peaks_from_samples_gives_the_strips_own_numbers_for_the_same_frames():
    """The music library draws a clip's waveform from samples it has just
    decoded, through ``peaks_from_samples``; the strip reads its file in
    chunks through ``compute_peaks``. One bucket rule, two readers: the
    numbers must not differ - mono, or stereo mixed down by the strip's own
    rule (the louder channel per frame, sign kept)."""
    pid = _video(with_audio=False)
    rng = np.random.default_rng(3)
    mono = rng.integers(-32768, 32767, size=4000, dtype=np.int64).astype("<i2")
    path = _wav(waveform.audio_path(pid), mono, rate=10)
    strip = waveform.compute_peaks(path)
    mine = waveform.peaks_from_samples(mono, 10)
    assert mine == strip["peaks"] and len(mine) == strip["buckets"] == 3200, "400 s at 8 a second"
    assert waveform.peaks_payload(mine, 400.0, 10) == strip, "and the same response shape"

    left = rng.integers(-32768, 32767, size=1000, dtype=np.int64).astype("<i2")
    right = rng.integers(-32768, 32767, size=1000, dtype=np.int64).astype("<i2")
    interleaved = np.empty(2000, dtype="<i2")
    interleaved[0::2] = left
    interleaved[1::2] = right
    path = _wav(waveform.audio_path(pid), interleaved, rate=1, channels=2)
    frames = np.stack([left, right], axis=1)
    louder = frames[np.arange(1000), np.abs(frames.astype(np.int32)).argmax(axis=1)]
    assert waveform.peaks_from_samples(louder, 1) == waveform.compute_peaks(path)["peaks"]

    assert waveform.peaks_from_samples([], 1000) == [] and waveform.peaks_from_samples(mono, 0) == []
    assert waveform.bucket_count(10.0, bucket_seconds=0.01) == 1000, "the bucket width is a parameter, 125 ms by default"
    assert waveform.BUCKET_SECONDS == 0.125


def test_a_single_bucket_wider_than_a_chunk_is_still_read(monkeypatch):
    """The other side of that loop: when one bucket does not fit in a chunk it
    is read whole rather than the loop spinning on an empty range."""
    pid = _video(with_audio=False)
    samples = np.full(4000, 8192, dtype="<i2")
    path = _wav(waveform.audio_path(pid), samples, rate=1)  # 4000 s -> capped, 4000 frames

    monkeypatch.setattr(waveform, "CHUNK_FRAMES", 1)
    payload = waveform.compute_peaks(path)
    assert len(payload["peaks"]) == payload["buckets"]
    assert set(payload["peaks"]) == {63}, "8192/32768 of full scale, in every bucket"


# ── the shapes of PCM we accept ──────────────────────────────────────────────

def test_a_stereo_file_is_mixed_down_to_one_magnitude_per_frame(client):
    """``audio.wav`` is mono, but a project can be restored from a backup or an
    older import, and reading a stereo file as mono would halve its length and
    put every sentence in the wrong place. Per FRAME, and the LOUDER channel
    wins - abs() before the max, or a quiet positive channel would beat a loud
    negative one."""
    pid = _video(with_audio=False)
    frames = 1000
    left = np.zeros(frames, dtype=np.int32)
    right = np.zeros(frames, dtype=np.int32)
    left[10] = 8192
    right[10] = -24576          # the louder of the two, and negative
    interleaved = np.empty(frames * 2, dtype="<i2")
    interleaved[0::2] = left
    interleaved[1::2] = right
    path = _wav(waveform.audio_path(pid), interleaved, rate=1, channels=2)

    payload = waveform.compute_peaks(path)
    assert payload["duration"] == pytest.approx(1000.0), "frames, not samples"
    assert payload["buckets"] == 1000
    assert payload["peaks"][10] == 191, "24576/32768 of full scale, from the negative channel"
    assert payload["peaks"][11] == 0


def test_an_eight_bit_file_is_unsigned_and_is_shifted_rather_than_viewed(client):
    """8-bit WAV is unsigned by the format's own definition: 128 is silence.
    Read as signed, silence would come back as the loudest thing in the file."""
    pid = _video(with_audio=False)
    samples = np.full(1000, 128, dtype=np.uint8)
    samples[5] = 255            # full positive
    samples[6] = 0              # full negative
    path = _wav(waveform.audio_path(pid), samples, rate=1, width=1)

    payload = waveform.compute_peaks(path)
    assert payload["peaks"][0] == 0, "128 is silence, not full scale"
    assert payload["peaks"][5] == 253, "127/128 of full scale"
    assert payload["peaks"][6] == 255


# ── the cache ────────────────────────────────────────────────────────────────

def test_the_peaks_are_computed_once_and_read_back_from_beside_the_audio(client, monkeypatch):
    pid = _video()
    calls = []
    real = waveform.compute_peaks
    monkeypatch.setattr(waveform, "compute_peaks", lambda path: (calls.append(path), real(path))[1])

    first = client.get(f"/api/projects/{pid}/waveform")
    second = client.get(f"/api/projects/{pid}/waveform")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1, "the second request was served from the cache"

    cache = store.PROJECTS_DIR / pid / waveform.CACHE_NAME
    held = json.loads(cache.read_text(encoding="utf-8"))
    assert held["size"] == waveform.audio_path(pid).stat().st_size
    assert held["mtime_ns"] == waveform.audio_path(pid).stat().st_mtime_ns


def test_re_extracting_the_audio_invalidates_the_cache_by_itself(client, monkeypatch):
    """Keyed on the source's mtime AND size, so a re-transcription that rewrites
    ``audio.wav`` needs nobody to remember to clear anything."""
    pid = _video()
    calls = []
    real = waveform.compute_peaks
    monkeypatch.setattr(waveform, "compute_peaks", lambda path: (calls.append(path), real(path))[1])

    assert waveform.peaks_for(pid)["buckets"] == 800

    # A different LENGTH of audio: a different size.
    _wav(waveform.audio_path(pid), np.zeros(16_000, dtype="<i2"), rate=1000)
    assert waveform.peaks_for(pid)["duration"] == pytest.approx(16.0)
    assert len(calls) == 2

    # And the same size rewritten in place: only the mtime moves.
    path = waveform.audio_path(pid)
    size_before = path.stat().st_size
    _wav(path, np.full(16_000, 4096, dtype="<i2"), rate=1000)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    assert path.stat().st_size == size_before
    assert max(waveform.peaks_for(pid)["peaks"]) == 31
    assert len(calls) == 3


def test_a_damaged_cache_file_is_recomputed_rather_than_answered_with_a_500(client):
    pid = _video()
    assert client.get(f"/api/projects/{pid}/waveform").status_code == 200
    (store.PROJECTS_DIR / pid / waveform.CACHE_NAME).write_text("{ not json", encoding="utf-8")

    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 200 and r.json()["buckets"] == 800


def test_a_cache_that_cannot_be_written_still_answers(client, monkeypatch):
    """The answer is already computed and correct; failing to memoise it is not
    a reason to fail the request."""
    pid = _video()
    monkeypatch.setattr(store, "replace_with_retry", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 200 and r.json()["buckets"] == 800
    assert not (store.PROJECTS_DIR / pid / waveform.CACHE_NAME).exists()


# ── the answers ──────────────────────────────────────────────────────────────

def test_a_deck_has_no_waveform(client):
    deck = store.import_upload("deck.pptx", b"pptx-bytes")["id"]
    r = client.get(f"/api/projects/{deck}/waveform")
    assert r.status_code == 400 and "Only video projects" in r.json()["detail"]


def test_an_untranscribed_video_says_what_to_do_about_it(client):
    """The audio is extracted BY the transcribe step, so the answer names it -
    the same wording the original-audio track download uses."""
    pid = store.import_upload("other.mp4", b"video-bytes")["id"]
    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 404
    assert "Transcribe the video first" in r.json()["detail"]


def test_a_missing_project_is_a_404(client):
    assert client.get("/api/projects/aabbccddeeff/waveform").status_code == 404


def test_audio_that_is_not_pcm_we_can_read_is_a_422_not_a_500(client):
    """The file is there and we simply cannot draw it; the timeline falls back
    to blocks with no waveform behind them rather than to an error page."""
    pid = _video(with_audio=False)
    path = waveform.audio_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFFnot-a-wave-file-at-all")

    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 422, r.text
    assert "audio.wav" in r.json()["detail"]


def test_a_sample_width_the_module_cannot_read_is_also_a_422(client):
    """24-bit PCM: ``wave`` opens it happily and numpy has no 3-byte integer."""
    pid = _video(with_audio=False)
    path = waveform.audio_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(3)
        wf.setframerate(1000)
        wf.writeframes(b"\x00\x00\x00" * 4000)

    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 422 and "sample width" in r.json()["detail"]


def test_a_truncated_file_is_drawn_at_the_length_it_really_has(client):
    """A WAV header carries the data chunk's claimed size, and a file truncated
    after it was written keeps the claim. Drawn on trust, the missing tail came
    back as a run of zeroes - a confident flat band saying "the speaker was
    silent here" over a stretch of recording we do not have. The one thing a
    waveform must never do, since the whole point is aiming a sentence at what
    the strip shows."""
    pid = _video(with_audio=False)
    path = _wav(waveform.audio_path(pid), np.full(8000, 16384, dtype="<i2"), rate=1000)
    whole = waveform.compute_peaks(path)
    assert whole["duration"] == pytest.approx(8.0)

    # Lop off the last three quarters of the samples, header untouched.
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) - 6000 * 2])

    payload = client.get(f"/api/projects/{pid}/waveform").json()
    assert payload["duration"] == pytest.approx(2.0), "the length that is really there"
    assert set(payload["peaks"]) == {127}, "and not one bucket of invented silence"
    # The plan's scale must agree with the strip's, or the blocks are drawn
    # against a timeline the waveform does not cover.
    assert waveform.duration_for(pid) == pytest.approx(2.0)


def test_a_file_whose_data_is_gone_entirely_is_a_422(client):
    pid = _video(with_audio=False)
    path = _wav(waveform.audio_path(pid), np.zeros(8000, dtype="<i2"), rate=1000)
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) - 8000 * 2])

    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 422 and "no frames" in r.json()["detail"]
    assert waveform.duration_for(pid) is None


def test_finding_the_real_end_costs_a_handful_of_seeks_not_a_read(client, monkeypatch):
    """Bisected rather than scanned: ~25 single-frame probes for a two-hour
    file, so an honest length costs nothing next to the read that follows."""
    pid = _video(with_audio=False)
    path = _wav(waveform.audio_path(pid), np.zeros(200_000, dtype="<i2"), rate=1000)
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) - 100_000 * 2])

    probes = []
    real = waveform._has_frame
    monkeypatch.setattr(waveform, "_has_frame",
                        lambda wf, i, n: (probes.append(i), real(wf, i, n))[1])
    assert waveform.duration_for(pid) == pytest.approx(100.0)
    assert len(probes) < 25, probes


def test_an_empty_wav_is_a_422_rather_than_a_division_by_zero(client):
    pid = _video(with_audio=False)
    _wav(waveform.audio_path(pid), np.zeros(0, dtype="<i2"), rate=1000)

    r = client.get(f"/api/projects/{pid}/waveform")
    assert r.status_code == 422 and "no frames" in r.json()["detail"]


# ── the length, on its own ───────────────────────────────────────────────────

def test_the_duration_comes_from_the_wav_header_and_never_from_ffprobe():
    """``duration_for`` is the header read the audition plan uses when it only
    wants the length. ffprobe may not exist in the packaged app at all, and the
    strip and everything drawn over it must share ONE scale - the scale of the
    file being drawn."""
    pid = _video(with_audio=False)
    _wav(waveform.audio_path(pid), np.zeros(45_500, dtype="<i2"), rate=1000)

    assert waveform.duration_for(pid) == pytest.approx(45.5)
    assert waveform.duration_for("aabbccddeeff") is None, "no project, no audio, no length"


def test_the_length_of_unreadable_audio_is_none_rather_than_an_exception():
    """A caller that only wants the length has a usable fallback and should not
    fail for want of a waveform."""
    pid = _video(with_audio=False)
    path = waveform.audio_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a wave file")

    assert waveform.duration_for(pid) is None


def test_reading_the_length_does_not_write_or_read_the_peaks_cache():
    """It is a header read - a few dozen bytes - so it must not drag the whole
    decode in behind it."""
    pid = _video()
    assert waveform.duration_for(pid) == pytest.approx(8.0)
    assert not (store.PROJECTS_DIR / pid / waveform.CACHE_NAME).exists()
