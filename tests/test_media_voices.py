"""``GET /api/voices?provider=...`` with the TTS generators mocked out.

Neither Microsoft's Edge service nor the Kokoro model is touched: the
generators are replaced at their module attributes (the endpoint imports them
lazily, so a monkeypatched attribute is what it sees), ``kokoro_model_present``
is pinned per test because the developer's machine may or may not hold the
model, and the config (the Kokoro voice cache lives there) is in-memory.
"""

import sys
import types

import pytest
from fastapi.testclient import TestClient

from api import store
from core import edge_tts_generator, kokoro_tts_generator
from core.tts_provider import Voice
from utils.config import config

# The real generator, kept before any test replaces the module attribute: the
# fakes delegate the curated (offline) list to it.
_REAL_KOKORO = kokoro_tts_generator.KokoroTTSGenerator


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A logged-in admin TestClient with the auth DB and the config isolated."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)

    from api.app import app

    with TestClient(app) as c:
        seeded = store.authenticate("admin", "admin")
        store.change_password(seeded["id"], "admin", must_change=False)
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200
        yield c


class _FakeKokoro:
    """Stands in for KokoroTTSGenerator: a fixed model voice list, no model load."""

    fetched = 0

    def model_voices(self):
        _FakeKokoro.fetched += 1
        return [
            Voice("af_heart", "Heart (American English (Female))", "American English (Female)"),
            Voice("bm_george", "George (British English (Male))", "British English (Male)"),
            Voice("jf_alpha", "Alpha", "Kokoro"),
        ]

    @staticmethod
    def curated_voices():
        return _REAL_KOKORO.curated_voices()


class _BrokenKokoro:
    """A model that is on disk but will not load: the strict listing raises."""

    def model_voices(self):
        raise RuntimeError("model load exploded")

    @staticmethod
    def curated_voices():
        return _REAL_KOKORO.curated_voices()


class _FakeEdge:
    fetched = 0

    def fetch_voices(self):
        _FakeEdge.fetched += 1
        return [
            edge_tts_generator.EdgeVoice("en-US-AriaNeural", "AriaNeural (en-US, Female)", "en-US", "Microsoft Aria - Female"),
            edge_tts_generator.EdgeVoice("en-GB-RyanNeural", "RyanNeural (en-GB, Male)", "en-GB", "Microsoft Ryan - Male"),
        ]


class _OfflineEdge:
    def fetch_voices(self):
        raise ConnectionError("no route to host")


def test_kokoro_voices_come_from_the_generator_and_are_cached(client, monkeypatch):
    monkeypatch.setattr(kokoro_tts_generator, "kokoro_model_present", lambda: True)
    monkeypatch.setattr(kokoro_tts_generator, "KokoroTTSGenerator", _FakeKokoro)
    _FakeKokoro.fetched = 0

    r = client.get("/api/voices?provider=kokoro")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "kokoro"
    assert body["error"] is None and body["notice"] is None
    assert body["voices"][0] == {
        "voice_id": "af_heart", "name": "Heart (American English (Female))", "locale": "en-US", "gender": "Female",
    }
    assert body["voices"][1]["locale"] == "en-GB" and body["voices"][1]["gender"] == "Male"
    assert body["voices"][2]["locale"] == "ja-JP" and body["voices"][2]["gender"] == "Female"
    assert _FakeKokoro.fetched == 1

    # The list went into the config cache (config.cache_kokoro_voices); the
    # next call is served from it without loading the model again - even if
    # the model could no longer be loaded.
    assert [v["voice_id"] for v in config._config["cached_kokoro_voices"]] == ["af_heart", "bm_george", "jf_alpha"]
    monkeypatch.setattr(kokoro_tts_generator, "KokoroTTSGenerator", _BrokenKokoro)
    again = client.get("/api/voices?provider=kokoro").json()
    assert [v["voice_id"] for v in again["voices"]] == ["af_heart", "bm_george", "jf_alpha"]
    assert _FakeKokoro.fetched == 1


def test_kokoro_without_the_model_lists_the_curated_voices_with_a_notice(client, monkeypatch):
    monkeypatch.setattr(kokoro_tts_generator, "kokoro_model_present", lambda: False)
    monkeypatch.setattr(kokoro_tts_generator, "KokoroTTSGenerator", _FakeKokoro)
    _FakeKokoro.fetched = 0

    body = client.get("/api/voices?provider=kokoro").json()
    assert body["error"] is None
    assert "downloads once" in body["notice"]
    assert _FakeKokoro.fetched == 0, "never loads (or downloads) the model"
    ids = [v["voice_id"] for v in body["voices"]]
    assert "af_heart" in ids and len(ids) == len(kokoro_tts_generator._FALLBACK_VOICES)
    assert "cached_kokoro_voices" not in config._config, "the fallback list is not cached as the model's"


def test_kokoro_import_failure_is_reported_not_a_500(client, monkeypatch):
    monkeypatch.setitem(sys.modules, "core.kokoro_tts_generator", None)  # import -> ImportError

    r = client.get("/api/voices?provider=kokoro")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "kokoro"
    assert body["voices"] == []
    assert body["error"].startswith("Kokoro is not available on this server")


def test_a_kokoro_model_that_will_not_load_is_reported_and_never_cached(client, monkeypatch):
    """Present-but-broken model: the strict listing raises, the route reports
    it (no curated list dressed up as the model's) and caches nothing, so the
    next call tries the model again rather than serving a wrong list for a week."""
    monkeypatch.setattr(kokoro_tts_generator, "kokoro_model_present", lambda: True)
    monkeypatch.setattr(kokoro_tts_generator, "KokoroTTSGenerator", _BrokenKokoro)

    body = client.get("/api/voices?provider=kokoro").json()
    assert body["voices"] == []
    assert "model load exploded" in body["error"]
    assert "cached_kokoro_voices" not in config._config


# -- the generator's own listing semantics ------------------------------------

def _model_on_disk(monkeypatch, names):
    monkeypatch.setattr(kokoro_tts_generator, "kokoro_model_present", lambda: True)
    monkeypatch.setattr(kokoro_tts_generator, "ensure_kokoro_model", lambda progress=None: ("m.onnx", "v.bin"))
    monkeypatch.setattr(kokoro_tts_generator, "_load_model", lambda m, v: types.SimpleNamespace(get_voices=lambda: names))


def test_model_voices_is_strict_and_fetch_voices_is_forgiving(monkeypatch):
    gen = _REAL_KOKORO()

    _model_on_disk(monkeypatch, ["bm_lewis", "af_sky"])
    assert [v.voice_id for v in gen.model_voices()] == ["af_sky", "bm_lewis"]
    assert gen.voices[0].name == "Sky (American English (Female))"
    assert [v.voice_id for v in gen.fetch_voices()] == ["af_sky", "bm_lewis"]

    # A model that lists nothing, or will not load, is an error for model_voices...
    _model_on_disk(monkeypatch, [])
    with pytest.raises(RuntimeError, match="lists no voices"):
        gen.model_voices()

    def boom(m, v):
        raise RuntimeError("bad onnx")

    monkeypatch.setattr(kokoro_tts_generator, "_load_model", boom)
    with pytest.raises(RuntimeError, match="bad onnx"):
        gen.model_voices()
    # ...while fetch_voices falls back to the curated list (the engine's contract).
    assert [v.voice_id for v in gen.fetch_voices()] == kokoro_tts_generator._FALLBACK_VOICES

    # An absent model is an error too - never a download.
    monkeypatch.setattr(kokoro_tts_generator, "kokoro_model_present", lambda: False)
    with pytest.raises(RuntimeError, match="not downloaded"):
        gen.model_voices()


# -- Edge ------------------------------------------------------------------------

def test_edge_voices_carry_locale_and_gender_and_are_cached(client, monkeypatch):
    monkeypatch.setattr(edge_tts_generator, "EdgeTTSGenerator", _FakeEdge)
    _FakeEdge.fetched = 0

    body = client.get("/api/voices?provider=edge_tts").json()
    assert body["provider"] == "edge_tts"
    assert body["error"] is None and body["notice"] is None
    assert body["voices"] == [
        {"voice_id": "en-US-AriaNeural", "name": "AriaNeural (en-US, Female)", "locale": "en-US", "gender": "Female"},
        {"voice_id": "en-GB-RyanNeural", "name": "RyanNeural (en-GB, Male)", "locale": "en-GB", "gender": "Male"},
    ]
    assert _FakeEdge.fetched == 1

    # Cached in the config (config.cache_edge_voices); the next call does not
    # go to Microsoft again - even offline.
    assert [v["voice_id"] for v in config._config["cached_edge_voices"]] == ["en-US-AriaNeural", "en-GB-RyanNeural"]
    monkeypatch.setattr(edge_tts_generator, "EdgeTTSGenerator", _OfflineEdge)
    again = client.get("/api/voices?provider=edge_tts").json()
    assert again["voices"] == body["voices"] and again["error"] is None
    assert _FakeEdge.fetched == 1


def test_edge_stale_cache_is_served_when_the_refresh_fails(client, monkeypatch):
    monkeypatch.setattr(edge_tts_generator, "EdgeTTSGenerator", _FakeEdge)
    client.get("/api/voices?provider=edge_tts")
    config._config["edge_voices_cached_at"] = 0  # a week ago and more
    monkeypatch.setattr(edge_tts_generator, "EdgeTTSGenerator", _OfflineEdge)

    body = client.get("/api/voices?provider=edge_tts").json()
    assert [v["voice_id"] for v in body["voices"]] == ["en-US-AriaNeural", "en-GB-RyanNeural"]
    assert body["error"] is None
    assert "no route to host" in body["notice"]


def test_edge_fetch_failure_with_nothing_cached_returns_an_empty_list_with_the_error(client, monkeypatch):
    monkeypatch.setattr(edge_tts_generator, "EdgeTTSGenerator", _OfflineEdge)

    r = client.get("/api/voices?provider=edge_tts")
    assert r.status_code == 200
    assert r.json()["voices"] == []
    assert "no route to host" in r.json()["error"]
    assert "cached_edge_voices" not in config._config


def test_default_provider_is_the_configured_one(client, monkeypatch):
    monkeypatch.setattr(edge_tts_generator, "EdgeTTSGenerator", _FakeEdge)
    monkeypatch.setattr(kokoro_tts_generator, "kokoro_model_present", lambda: True)
    monkeypatch.setattr(kokoro_tts_generator, "KokoroTTSGenerator", _FakeKokoro)

    assert client.get("/api/voices").json()["provider"] == "edge_tts"
    config._config["tts_provider"] = "kokoro"
    assert client.get("/api/voices").json()["provider"] == "kokoro"


def test_unknown_provider_is_a_400(client):
    r = client.get("/api/voices?provider=polly")
    assert r.status_code == 400
    assert "Unknown narration provider 'polly'" in r.json()["detail"]


def test_voices_require_a_session(client):
    assert TestClient(client.app).get("/api/voices?provider=kokoro").status_code == 401
