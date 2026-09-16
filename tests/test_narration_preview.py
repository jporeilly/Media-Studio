"""Hearing one sentence (narration timeline, phase 2).

``GET /api/projects/{pid}/transcript/{index}/preview`` synthesises ONE stored
transcript sentence and hands back the audio, so a per-sentence voice or speed
can be heard before a whole re-voice is run for it.

Three decisions are nailed down here rather than left to a code reading:

- **the words come from the store, never from the caller.** That is what makes a
  GET honest (nothing is written, the browser may cache it, and the audit guard
  - which counts every non-GET as mutating - is entitled to ignore it).
- **it is not a job.** ``services.jobs`` allows one job per project, so a
  preview-as-job would block the very re-voice it is auditioning for.
- **the provider's failure modes are bounded.** ``generate_audio`` swallows
  every exception and returns None, and Edge's own ceiling is 120 s: both must
  come back as a readable 502, never a 500 and never a two-minute hang.
- **nothing half-written ever reaches the shared cache.** Both real providers
  write their audio IN PLACE, chunk by chunk, at whatever path they are handed,
  and the only completeness check anywhere is that the file exists. Handed the
  cache entry itself, a dropped stream would leave a stump at that key forever -
  read by every later preview and, worse, copied out by every later re-voice as
  if it were whole. The fake below therefore writes the way they do (open,
  write, wait, write) so a half-written file is genuinely observable in a test,
  rather than appearing atomically and hiding the bug it exists to catch.
"""

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs, narration
from services import projects as store
from utils import helpers
from utils.config import config

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "First sentence."},
    {"start": 5.0, "end": 7.0, "text": "Second sentence."},
    {"start": 10.0, "end": 12.0, "text": "Third sentence."},
]

STUDIO_EDGE_VOICE = "en-US-AriaNeural"  # utils.config.Config.edge_tts_voice's default
STUDIO_KOKORO_VOICE = "af_heart"        # ... and kokoro_voice's


class FakeTTS:
    """A provider that writes the way the real two do: in place, in chunks, at
    whatever path it is handed, with no cache handling of its own when
    ``use_cache`` is False.

    ``result`` picks the ending: ``audio`` finishes the file; ``drops`` writes
    the opening and then returns None with the stump still on disk, which is
    exactly what ``EdgeTTSGenerator.generate_audio`` does when a stream fails
    (it logs and returns None, leaving whatever was written); ``nothing``
    returns None having written no file at all; ``empty`` leaves a zero-byte
    file and claims success.
    """

    HEAD = b"ID3\x03fake-mp3-"
    TAIL = b"bytes-to-the-very-end"
    AUDIO = HEAD + TAIL

    def __init__(self, provider_id="edge_tts", result="audio", delay=0.0):
        self.provider_id = provider_id
        self.result = result
        self.delay = delay
        self.calls: list[dict] = []
        self.output_paths: list = []
        self.used_cache: list = []

    def generate_audio(self, text, voice_id, output_path=None, speed=1.0, use_cache=True, **_ignored):
        self.calls.append({"text": text, "voice_id": voice_id, "speed": speed})
        self.output_paths.append(output_path)
        self.used_cache.append(use_cache)
        if self.result == "nothing":
            return None
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            if self.result == "empty":
                return path
            fh.write(self.HEAD)
            fh.flush()
            if self.delay:
                time.sleep(self.delay)
            if self.result == "drops":
                return None
            fh.write(self.TAIL)
        return path


def _entry(text="First sentence.", voice=STUDIO_EDGE_VOICE, speed=1.0, provider="edge_tts") -> Path:
    """The shared cache entry a preview of that sentence publishes to - the same
    one the render looks up."""
    return narration.cache_path_for(provider, text, voice, speed)


def _parts(entry: Path) -> list[str]:
    """Any half-written scratch files left in the cache directory."""
    if not entry.parent.exists():
        return []
    return sorted(p.name for p in entry.parent.iterdir() if p.name.endswith(".part"))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(store, "_locks", {})
    # The TTS cache is shared by the render and the preview; a test must not
    # write into the developer's real data/cache (nothing ever sweeps it).
    monkeypatch.setattr(helpers, "CACHE_DIR", tmp_path / "cache")


@pytest.fixture
def tts(monkeypatch):
    """The provider every preview in this module talks to."""
    fake = FakeTTS()
    monkeypatch.setattr("core.tts_provider.get_tts_provider", lambda provider_id=None: fake)
    return fake


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _video(segments=None) -> str:
    rec = store.import_upload("clip.mp4", b"video-bytes")
    store.set_transcript(rec["id"], [dict(s) for s in (SEGMENTS if segments is None else segments)])
    return rec["id"]


def _preview(client: TestClient, pid: str, index: int = 0, **params):
    return client.get(f"/api/projects/{pid}/transcript/{index}/preview", params=params or None)


# ── 200, and where the defaults come from ────────────────────────────────────

def test_a_bare_preview_speaks_the_stored_sentence_at_the_studio_defaults(client, tts):
    """A parameterless call means "play this line the way the render will": the
    sentence has no overrides, so the studio's provider, voice and the re-voice
    request's own default speed are what it gets."""
    pid = _video()

    r = _preview(client, pid, 1)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/mpeg"
    assert r.content == FakeTTS.AUDIO
    assert tts.calls == [{"text": "Second sentence.", "voice_id": STUDIO_EDGE_VOICE, "speed": 1.0}]


def test_the_sentences_own_voice_and_speed_win_over_the_jobs(client, tts):
    """The same precedence the render uses (``_revoice_video``): the job carries
    a voice and a speed, and a sentence that has its own overrides them. A
    preview that inverted this would be advertising a render that will not
    happen."""
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/0",
                        json={"voice": "en-GB-RyanNeural", "speed": 1.15}).status_code == 200

    r = _preview(client, pid, 0, provider="edge_tts", voice="en-US-JennyNeural", speed=0.8)
    assert r.status_code == 200, r.text
    assert tts.calls == [{"text": "First sentence.", "voice_id": "en-GB-RyanNeural", "speed": 1.15}]


def test_the_jobs_voice_and_speed_are_used_when_the_sentence_has_none(client, tts):
    """...which is how the Play button reproduces what the Re-voice card is
    about to do for every sentence that carries no override of its own."""
    pid = _video()

    r = _preview(client, pid, 2, provider="edge_tts", voice="en-US-JennyNeural", speed=0.85)
    assert r.status_code == 200, r.text
    assert tts.calls == [{"text": "Third sentence.", "voice_id": "en-US-JennyNeural", "speed": 0.85}]


def test_a_stored_voice_from_the_other_provider_falls_back_silently(client, tts, monkeypatch):
    """``effective_voice`` is the rule the slide editor already relies on: a
    stored override that belongs to the other provider is dropped for the
    provider-correct default rather than failing. The preview has to follow it,
    because that is exactly what the render will do with that sentence."""
    monkeypatch.setattr("core.kokoro_tts_generator.kokoro_model_present", lambda: True)
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"voice": "en-GB-RyanNeural"}).status_code == 200

    r = _preview(client, pid, 0, provider="kokoro")
    assert r.status_code == 200, r.text
    assert tts.calls[0]["voice_id"] == STUDIO_KOKORO_VOICE, "the Edge id is dropped, not sent to Kokoro"


def test_a_stored_provider_never_switches_the_engine_the_preview_runs_on(client, tts):
    """A segment's stored ``provider`` records which provider its stored VOICE
    belongs to, and nothing else: no part of the render reads it, which resolves
    the provider from the job else the studio config. Honouring it here would
    make the preview diverge from the render in the case that sounds most
    helpful - previewing this sentence in Kokoro while the re-voice speaks it in
    the studio's Edge default."""
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/0",
                        json={"voice": "bm_george", "provider": "kokoro"}).status_code == 200

    assert _preview(client, pid, 0).status_code == 200
    assert tts.calls[0]["voice_id"] == STUDIO_EDGE_VOICE, (
        "the studio is on Edge, so the render would speak the Edge default and so must the preview"
    )


# ── the text is the store's, never the caller's ──────────────────────────────

def test_the_words_come_from_the_store_and_not_from_the_caller(client, tts):
    """The route takes no text. An unknown query parameter is ignored and a body
    smuggled onto the GET changes nothing: what is spoken is what is saved,
    which is the rule that lets the transcript editor's explicit Save stand as
    "you can only hear what you have saved"."""
    pid = _video()

    r = client.request(
        "GET", f"/api/projects/{pid}/transcript/0/preview",
        params={"text": "Say this instead.", "sentence": "or this"},
        json={"text": "or even this"},
    )
    assert r.status_code == 200, r.text
    assert [c["text"] for c in tts.calls] == ["First sentence."]


def test_a_long_sentence_is_capped_rather_than_synthesised_whole(client, tts):
    """One press of a Play button must not become a minutes-long synthesis. No
    real Whisper sentence comes near the cap."""
    pid = _video([{"start": 0.0, "end": 9.0, "text": "word " * 2000}])

    assert _preview(client, pid, 0).status_code == 200
    assert len(tts.calls[0]["text"]) == narration.MAX_PREVIEW_CHARS


def test_an_empty_sentence_has_nothing_to_play(client, tts):
    pid = _video([{"start": 0.0, "end": 2.0, "text": "   "}])

    r = _preview(client, pid, 0)
    assert r.status_code == 400 and "no words to speak" in r.json()["detail"]
    assert tts.calls == [], "nothing was sent to the provider"


# ── 400 ──────────────────────────────────────────────────────────────────────

def test_an_unknown_provider_is_refused(client, tts):
    pid = _video()
    r = _preview(client, pid, 0, provider="elevenlabs")
    assert r.status_code == 400 and "Unknown narration provider" in r.json()["detail"]
    assert tts.calls == []


def test_an_asked_for_voice_from_the_other_provider_is_refused(client, tts):
    """Asked for HERE it is a mistake worth naming - the same answer generate
    and re-voice give. Only a STORED one falls back silently."""
    pid = _video()
    r = _preview(client, pid, 0, provider="kokoro", voice="en-US-AriaNeural")
    assert r.status_code == 400 and "looks like an Edge TTS voice" in r.json()["detail"]
    assert tts.calls == []


def test_a_deck_has_no_transcript_to_preview(client, tts):
    deck = store.import_upload("deck.pptx", b"pptx-bytes")["id"]
    r = _preview(client, deck, 0)
    assert r.status_code == 400 and "Only video projects" in r.json()["detail"]


def test_a_speed_outside_the_stored_bounds_is_refused(client, tts):
    """The same bounds ``SegmentOverride`` enforces, so the preview cannot play
    a speed the sentence could never be saved with."""
    pid = _video()
    for bad in (0.4, 2.1):
        assert _preview(client, pid, 0, speed=bad).status_code == 422, bad
    assert tts.calls == []


# ── 404 ──────────────────────────────────────────────────────────────────────

def test_an_index_past_the_end_is_a_404_naming_the_range(client, tts):
    """A GET of a sentence that is not there is a 404 - unlike the PATCH, where
    the same index is a bad argument to a write and stays the 400 phase 1
    shipped."""
    pid = _video()
    r = _preview(client, pid, 9)
    assert r.status_code == 404 and "0 to 2" in r.json()["detail"]
    assert client.patch(f"/api/projects/{pid}/transcript/9", json={"offset": 1.0}).status_code == 400


def test_an_untranscribed_video_has_nothing_to_play(client, tts):
    pid = store.import_upload("other.mp4", b"video-bytes")["id"]
    r = _preview(client, pid, 0)
    assert r.status_code == 404 and "transcribe it first" in r.json()["detail"]


def test_a_missing_project_is_a_404(client, tts):
    assert _preview(client, "aabbccddeeff", 0).status_code == 404


# ── 409: Kokoro's model ──────────────────────────────────────────────────────

def test_kokoro_without_its_model_answers_409_and_downloads_nothing(client, tts, monkeypatch):
    """The first Kokoro synthesis pulls ~340 MB. Pressing Play must not start
    that and then hold the request until it finishes, so the model is probed
    with the stat-only ``kokoro_model_present`` and the answer says what is
    missing."""
    monkeypatch.setattr("core.kokoro_tts_generator.kokoro_model_present", lambda: False)
    pid = _video()

    r = _preview(client, pid, 0, provider="kokoro")
    assert r.status_code == 409, r.text
    assert "about 340 MB" in r.json()["detail"], r.json()
    assert tts.calls == [], "the provider was never asked, so nothing downloaded"


# ── 502: the provider answered nothing, or nothing in time ───────────────────

def test_a_provider_that_returns_nothing_is_a_502_with_a_reason(client, monkeypatch):
    """``generate_audio`` swallows every exception and returns None, so without
    this the route would hand back a file path of None and answer 500."""
    fake = FakeTTS(result="nothing")
    monkeypatch.setattr("core.tts_provider.get_tts_provider", lambda provider_id=None: fake)
    pid = _video()

    r = _preview(client, pid, 0)
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert "Edge TTS" in detail and STUDIO_EDGE_VOICE in detail, detail


def test_a_provider_that_hangs_is_waited_on_for_a_bounded_time(client, monkeypatch):
    """Edge's own ceiling is 120 s and it has no timeout argument, so the only
    way to stop waiting is to stop waiting. The abandoned thread keeps its own
    ``.part`` file: it publishes it when it eventually finishes - so the press
    nobody waited for still pays for the next one - and leaves no litter."""
    fake = FakeTTS(delay=1.0)
    monkeypatch.setattr("core.tts_provider.get_tts_provider", lambda provider_id=None: fake)
    monkeypatch.setattr(narration, "PREVIEW_TIMEOUT_SECONDS", 0.05)
    pid = _video()
    entry = _entry()

    started = time.monotonic()
    r = _preview(client, pid, 0)
    assert r.status_code == 502, r.text
    assert time.monotonic() - started < 10, "the request waited for the provider's own ceiling"
    assert not entry.exists(), "the cache entry must not exist while the clip is still being written"

    deadline = time.monotonic() + 10
    while not entry.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert entry.read_bytes() == FakeTTS.AUDIO, "the abandoned press published a COMPLETE clip"
    assert _parts(entry) == [], "and left no .part file behind"


# ── not a job, and nothing is written ────────────────────────────────────────

def test_a_preview_is_not_a_job(client, tts):
    """Trap 14: one job per project, so a preview-as-job would refuse (or block)
    the re-voice the user is auditioning for."""
    pid = _video()
    assert _preview(client, pid, 0).status_code == 200
    assert jobs.active_for(pid) is None


def test_a_preview_is_allowed_while_a_job_holds_the_project(client, tts, monkeypatch):
    """It writes nothing, so a 409 on it would be a refusal to let someone
    listen. Every WRITE of this record still takes ``require_idle``."""
    pid = _video()
    monkeypatch.setattr(
        jobs, "active_for",
        lambda project_id: {"id": "j1", "kind": "revoice", "status": "running"} if project_id == pid else None,
    )
    assert _preview(client, pid, 0).status_code == 200
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": 1.0}).status_code == 409


def test_a_preview_changes_nothing_on_the_project(client, tts):
    pid = _video()
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()

    assert _preview(client, pid, 0, provider="edge_tts", voice="en-US-JennyNeural", speed=1.3).status_code == 200
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before


# ── what it costs ────────────────────────────────────────────────────────────

def test_the_second_press_is_free_and_warms_the_render_s_cache(client, tts):
    """The synthesis cache is keyed on (text, voice, speed) and the render uses
    the SAME key, so a previewed sentence is not synthesised again when the
    re-voice runs. This is why the UI can promise that only the first press
    costs a round trip - and, per the spec's trap 19, why nothing sweeps
    ``data/cache``."""
    pid = _video()

    first = _preview(client, pid, 0)
    second = _preview(client, pid, 0)
    assert (first.status_code, second.status_code) == (200, 200)
    assert len(tts.calls) == 1, "the second press was served from the cache"

    entry = _entry()
    assert entry.exists(), "the preview wrote the entry the render will look for"
    assert entry.read_bytes() == first.content


# ── the shared cache is never given a half-written file ──────────────────────

def test_the_provider_is_handed_a_private_path_and_told_to_leave_the_cache_alone(client, tts):
    """The call-site half of the fix. Left to itself ``generate_audio`` streams
    the provider's chunks straight into the shared cache entry for the whole
    round trip - this preview is the only call site that could, and does not."""
    pid = _video()
    assert _preview(client, pid, 0).status_code == 200

    handed = Path(tts.output_paths[0])
    assert handed != _entry(), "the provider must never be handed the cache entry itself"
    assert handed.name.endswith(".part")
    assert tts.used_cache == [False], "the preview owns the cache read and the publish"
    assert _parts(_entry()) == [], "and the scratch file is gone afterwards"


def test_a_stream_that_drops_leaves_no_entry_behind_to_poison_the_cache(client, monkeypatch):
    """The permanent half of the bug. A provider that fails mid-stream leaves
    whatever it had written and returns None (Edge logs and returns None without
    removing it, and nothing sweeps the cache) - so had it been writing into the
    cache entry, that stump would have been served to every later preview AND
    copied out by every later re-voice, which checks only that the file exists.
    The sentence would then count as a success and the stump would be muxed into
    the finished video, straight past phase 1's failed-sentence counting."""
    dropping = FakeTTS(result="drops")
    monkeypatch.setattr("core.tts_provider.get_tts_provider", lambda provider_id=None: dropping)
    pid = _video()
    entry = _entry()

    assert _preview(client, pid, 0).status_code == 502
    assert not entry.exists(), "a failed synthesis must leave NOTHING at the cache key"
    assert _parts(entry) == [], "and no scratch file either"

    # And the next press, with the provider working again, gets real audio
    # rather than a stump nothing would ever have replaced.
    working = FakeTTS()
    monkeypatch.setattr("core.tts_provider.get_tts_provider", lambda provider_id=None: working)
    r = _preview(client, pid, 0)
    assert r.status_code == 200 and r.content == FakeTTS.AUDIO


def test_a_provider_that_writes_an_empty_file_is_refused(client, monkeypatch):
    """The cheap second guard: "the file exists" is the only completeness check
    the engine has anywhere, and zero bytes is not audio."""
    empty = FakeTTS(result="empty")
    monkeypatch.setattr("core.tts_provider.get_tts_provider", lambda provider_id=None: empty)
    pid = _video()

    assert _preview(client, pid, 0).status_code == 502
    assert not _entry().exists()


def test_an_empty_entry_already_in_the_cache_is_replaced_rather_than_served(client, tts):
    """Detecting a bad entry on READ, as far as it can honestly go: a zero-byte
    entry is removed and synthesised again. (A truncated-but-non-empty mp3 can
    only be told apart by decoding it, which is ffmpeg on every press to guard
    against an entry this code can no longer create.)"""
    pid = _video()
    entry = _entry()
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(b"")

    r = _preview(client, pid, 0)
    assert r.status_code == 200 and r.content == FakeTTS.AUDIO
    assert entry.read_bytes() == FakeTTS.AUDIO


def test_a_second_press_during_the_first_never_sees_a_half_written_entry():
    """The reproduced failure, at the service, because that is where the writing
    happens: two presses of the same sentence at once, with the cache entry
    watched throughout. Every observation must be the complete clip - never a
    prefix - and both presses must come back with the whole thing.

    The UI could produce this directly: its Play button only pauses when the
    element is already playing, so before this was fixed a second press while
    the button still said "Speaking…" fired a second request.
    """
    fake = FakeTTS(delay=0.4)
    import core.tts_provider as tts_provider
    original = tts_provider.get_tts_provider
    tts_provider.get_tts_provider = lambda provider_id=None: fake
    try:
        pid = _video()
        entry = _entry()
        start = threading.Barrier(3)
        results: list = []
        failures: list = []

        def press():
            start.wait()
            try:
                results.append(narration.preview_segment(pid, 0))
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(exc)

        threads = [threading.Thread(target=press) for _ in range(2)]
        for t in threads:
            t.start()
        start.wait()

        # The watcher's OWN read races the publish, and on Windows that is a
        # PermissionError rather than a short read: os.replace briefly leaves
        # neither name openable, which is the same transient refusal
        # ``replace_with_retry`` absorbs on the writing side. Skipping the
        # observation is correct and does not weaken the assertion below - what
        # is under test is that no observation is ever a PREFIX of the clip, and
        # an observation that could not be taken is not one. (Unguarded, this
        # failed about 4 runs in 25 and was the suite's only flake.)
        seen: list[bytes] = []
        deadline = time.monotonic() + 10
        while any(t.is_alive() for t in threads) and time.monotonic() < deadline:
            try:
                seen.append(entry.read_bytes())
            except (FileNotFoundError, PermissionError):
                pass
            time.sleep(0.005)
        for t in threads:
            t.join(10)

        assert failures == [], failures
        assert [p.read_bytes() for p in results] == [FakeTTS.AUDIO, FakeTTS.AUDIO]
        assert all(b == FakeTTS.AUDIO for b in seen), "the cache entry was observed half-written"
        assert entry.read_bytes() == FakeTTS.AUDIO
        assert _parts(entry) == []
    finally:
        tts_provider.get_tts_provider = original


def test_the_cache_key_is_the_one_the_real_generators_look_up():
    """The anti-rot pin on ``cache_path_for``. The key is stated twice - here and
    inside each generator - and the difference is real: Kokoro folds its active
    language into the voice half, Edge does not. So the entry is pre-created
    where the PREVIEW would publish it and each REAL generator is asked for the
    same audio: it can only answer from the cache if the two agree, and the
    cache-hit branch of both returns before any network or model access, so this
    runs offline and downloads nothing."""
    from core.edge_tts_generator import EdgeTTSGenerator
    from core.kokoro_tts_generator import KokoroTTSGenerator

    text, speed = "Pin the cache key.", 1.1
    for generator, provider_id, voice in (
        (EdgeTTSGenerator(), "edge_tts", STUDIO_EDGE_VOICE),
        (KokoroTTSGenerator(), "kokoro", STUDIO_KOKORO_VOICE),
    ):
        entry = narration.cache_path_for(provider_id, text, voice, speed)
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b"pinned-" + provider_id.encode())
        out = entry.parent / f"{provider_id}-out.mp3"

        assert generator.generate_audio(text=text, voice_id=voice, output_path=out, speed=speed) == out
        assert out.read_bytes() == entry.read_bytes(), (
            f"{provider_id} looked up a different cache key than services.narration.cache_path_for: "
            "the preview would write where nothing reads, and the render would synthesise again"
        )


# ── the shape of the route ───────────────────────────────────────────────────

def test_the_preview_is_a_get_so_the_audit_guard_need_not_name_it(routes):
    """Deliberate, and recorded here: the audit guard counts every non-GET as
    mutating (``tests/test_audit.py``), and a preview records no user-visible
    change. Made a POST - to preview unsaved text, say - it would need a written
    AUDIT_EXEMPT entry."""
    methods = {r.method for r in routes if r.path.endswith("/transcript/{index}/preview")}
    assert methods == {"GET"}, methods
