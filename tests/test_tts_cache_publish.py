"""The shared TTS cache is only ever written by an atomic rename.

Both generators mirror a finished clip into ``data/cache`` so the next
synthesis of the same (text, voice, speed) is a file copy - that is what makes
a preview free the second time, and what makes a re-voice reuse what the
narration timeline already auditioned. They did it with a plain
``shutil.copy`` straight onto the shared key.

The only completeness check anywhere is that the file exists. So a copy
interrupted part way - a crash, a full disk, a cancelled job, the app being
closed - leaves a TRUNCATED entry at that key **forever**: nothing sweeps
``data/cache``, every later preview serves it, and every later re-voice copies
it out, sees a file, and muxes the stump into the finished video as a
successful sentence, straight past the failed-sentence counting.

It was always a race the render could lose. What makes it worth fixing now is
that the narration timeline drives the same path from a browser, so a re-voice,
a row's Play and a whole-transcript audition can all be filling the same keys at
once.

The fix is at the GENERATORS rather than at the new caller, because that is
where the write is: fixing it there fixes the render too.
"""

import shutil
import sys
import types
from pathlib import Path

import pytest

from utils import helpers
from utils.helpers import get_cache_path, publish_to_cache

AUDIO = b"ID3\x03" + b"complete-clip-bytes" * 8


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A cache of this test's own: nothing may write into the developer's real
    ``data/cache``, which nothing ever sweeps."""
    monkeypatch.setattr(helpers, "CACHE_DIR", tmp_path / "cache")
    return tmp_path


def _parts(cache_dir: Path) -> list[str]:
    if not cache_dir.exists():
        return []
    return sorted(p.name for p in cache_dir.iterdir() if p.name.endswith(".part"))


# ── publish_to_cache itself ──────────────────────────────────────────────────

def test_a_clip_appears_at_its_key_whole_or_not_at_all(tmp_path, monkeypatch):
    """The reproduced failure, at the primitive. The copy is watched byte by
    byte while it runs, and the key is read throughout: every observation must
    be the complete clip or nothing, never a prefix."""
    source = tmp_path / "seg_0001.mp3"
    source.write_bytes(AUDIO)
    entry = get_cache_path("A sentence.", "en-US-AriaNeural", speed=1.0,
                           stability=0, similarity_boost=0, style=0)
    seen: list[bytes] = []

    real_copyfile = shutil.copyfile

    def _watched(src, dst, **kwargs):
        # Copy in two halves, looking at the shared key in between - which is
        # exactly what a copy interrupted part way looks like from outside.
        data = Path(src).read_bytes()
        Path(dst).write_bytes(data[: len(data) // 2])
        seen.append(entry.read_bytes() if entry.exists() else b"")
        return real_copyfile(src, dst, **kwargs)

    monkeypatch.setattr(helpers.shutil, "copyfile", _watched)
    assert publish_to_cache(source, entry) is True

    assert seen == [b""], "the key was written before the copy had finished"
    assert entry.read_bytes() == AUDIO
    assert _parts(entry.parent) == [], "and no scratch file was left behind"


def test_a_copy_that_fails_leaves_nothing_at_the_key(tmp_path, monkeypatch):
    """The permanent half of the bug: a stump at the key is read by every later
    preview AND copied out by every later re-voice, which counts the sentence a
    success."""
    source = tmp_path / "seg_0001.mp3"
    source.write_bytes(AUDIO)
    entry = get_cache_path("Interrupted.", "en-US-AriaNeural", speed=1.0,
                           stability=0, similarity_boost=0, style=0)

    def _dies(src, dst, **kwargs):
        Path(dst).write_bytes(AUDIO[:9])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(helpers.shutil, "copyfile", _dies)
    assert publish_to_cache(source, entry) is False

    assert not entry.exists(), "a failed copy must leave NOTHING at the cache key"
    assert _parts(entry.parent) == []


def test_publishing_never_raises_so_a_good_synthesis_is_never_reported_as_a_failure(tmp_path, monkeypatch):
    """The cache is an optimisation. Before this, a copy error was caught by the
    generator's own blanket ``except`` and turned into "this sentence produced no
    audio" - losing a clip that had just been synthesised correctly."""
    source = tmp_path / "seg_0001.mp3"
    source.write_bytes(AUDIO)
    entry = get_cache_path("Unwritable.", "en-US-AriaNeural", speed=1.0,
                           stability=0, similarity_boost=0, style=0)
    monkeypatch.setattr(helpers.shutil, "copyfile",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    assert publish_to_cache(source, entry) is False  # and did not raise


def test_a_writer_that_lost_the_race_is_not_a_failure(tmp_path, monkeypatch):
    """The key is derived from the text, the voice and the speed, so an entry
    already sitting there is the same audio by construction: whoever got there
    first published the very bytes this call would have."""
    source = tmp_path / "seg_0001.mp3"
    source.write_bytes(AUDIO)
    entry = get_cache_path("Raced.", "en-US-AriaNeural", speed=1.0,
                           stability=0, similarity_boost=0, style=0)
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_bytes(AUDIO)

    monkeypatch.setattr(helpers, "replace_with_retry",
                        lambda tmp, path: (_ for _ in ()).throw(PermissionError("held")))
    assert publish_to_cache(source, entry) is True
    assert entry.read_bytes() == AUDIO
    assert _parts(entry.parent) == []


# ── and both generators really use it ────────────────────────────────────────

def test_edge_mirrors_its_output_into_the_cache_atomically(tmp_path, monkeypatch):
    """Edge streams its audio to the path it is handed and then copies that into
    the cache. It is the copy that is under test, so the network half is faked
    and nothing is downloaded."""
    import core.edge_tts_generator as edge

    fake = types.ModuleType("edge_tts")

    class Communicate:
        def __init__(self, text, voice, rate=None):
            self.args = (text, voice, rate)

        def save(self, path):
            return ("save", path)

    fake.Communicate = Communicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake)
    monkeypatch.setattr(edge, "_run_async", lambda job, timeout=120.0: Path(job[1]).write_bytes(AUDIO))

    published: list[tuple] = []
    real = edge.publish_to_cache
    monkeypatch.setattr(edge, "publish_to_cache",
                        lambda src, dst: published.append((Path(src), Path(dst))) or real(src, dst))

    out = tmp_path / "seg_0001.mp3"
    assert edge.EdgeTTSGenerator().generate_audio(
        text="Cache me.", voice_id="en-US-AriaNeural", output_path=out, speed=1.0) == out

    entry = get_cache_path("Cache me.", "en-US-AriaNeural", speed=1.0,
                           stability=0, similarity_boost=0, style=0)
    assert published == [(out, entry)], "the mirror must go through the atomic publish"
    assert entry.read_bytes() == AUDIO
    assert _parts(entry.parent) == []


def test_kokoro_mirrors_its_output_into_the_cache_atomically(tmp_path, monkeypatch):
    """The same write, in the other provider - it had the identical
    ``shutil.copy`` onto the shared key. The model is faked; nothing is
    downloaded and no ONNX runtime is loaded."""
    import core.kokoro_tts_generator as kokoro

    monkeypatch.setattr(kokoro, "ensure_kokoro_model", lambda *a, **k: (Path("model"), Path("voices")))
    monkeypatch.setattr(kokoro, "_load_model", lambda *a, **k: types.SimpleNamespace(
        create=lambda text, voice, speed, lang: ([0.0], 24000)))
    monkeypatch.setattr(kokoro.KokoroTTSGenerator, "_write_audio",
                        lambda self, samples, rate, path: Path(path).write_bytes(AUDIO))

    published: list[tuple] = []
    real = kokoro.publish_to_cache
    monkeypatch.setattr(kokoro, "publish_to_cache",
                        lambda src, dst: published.append((Path(src), Path(dst))) or real(src, dst))

    out = tmp_path / "seg_0001.mp3"
    assert kokoro.KokoroTTSGenerator().generate_audio(
        text="Cache me.", voice_id="af_heart", output_path=out, speed=1.0) == out

    assert len(published) == 1 and published[0][0] == out
    assert published[0][1].read_bytes() == AUDIO
    assert _parts(published[0][1].parent) == []


def test_neither_generator_copies_onto_the_cache_key_in_the_open():
    """The anti-rot guard. This is a write nobody watching a diff would flag -
    it is one line, it looks like a copy, and everything keeps working until the
    day a copy is interrupted and the truncated entry is permanent."""
    root = Path(__file__).resolve().parent.parent
    for name in ("edge_tts_generator", "kokoro_tts_generator"):
        source = (root / "core" / f"{name}.py").read_text(encoding="utf-8")
        assert "shutil.copy(output_path, cache_path)" not in source, (
            f"core/{name}.py writes the shared cache entry in the open again; an "
            "interrupted copy leaves a truncated clip at that key forever"
        )
        assert "publish_to_cache(output_path, cache_path)" in source


def test_the_retry_loop_has_one_home():
    """``services.projects.replace_with_retry`` was the repo's single
    write-a-temp-then-rename idiom and stays importable from there; the
    implementation moved to ``utils.helpers`` only because ``core`` needs it and
    must not import ``services``."""
    from services import projects as store

    assert store.replace_with_retry is helpers.replace_with_retry
