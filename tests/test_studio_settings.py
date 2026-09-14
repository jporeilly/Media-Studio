"""Settings › Studio: the service (``services/studio_settings.py``) and
``GET``/``PUT /api/settings/studio``.

The config is an in-memory dict per test (never ``data/config.json``) and
``config.save`` is a no-op, so the round trips below prove what would be
persisted without touching the developer's own settings.
"""

import pytest
from fastapi.testclient import TestClient

from api import store
from services import studio_settings
from utils.config import DEFAULT_CONFIG, config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A throwaway auth database and an in-memory config."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


# -- the service -----------------------------------------------------------

def test_defaults_when_nothing_is_stored():
    s = studio_settings.get_settings()
    assert s == {
        "tts_provider": "edge_tts",
        "edge_tts_voice": "en-US-AriaNeural",
        "kokoro_voice": "af_heart",
        "kokoro_lang": "en-us",
        "whisper_model": "",
        "ollama_model": "llama3",  # what services.revoice translates with when unset
        "output_folder": DEFAULT_CONFIG["output_folder"],
        "transition_pause": 0.0,
        "music_volume": 0.25,
        "slide_transition": "none",
        "transition_duration": 0.5,
        "watermark_text": "",
        "watermark_position": "bottom-right",
        "watermark_opacity": 0.5,
    }
    # The generation defaults are the engine's own (utils/config.py DEFAULT_CONFIG).
    for key in ("slide_transition", "transition_duration", "watermark_text", "watermark_position", "watermark_opacity"):
        assert s[key] == DEFAULT_CONFIG[key], key


def test_update_round_trips_through_config(tmp_path, monkeypatch):
    saves = []
    monkeypatch.setattr(config, "save", lambda: saves.append(1))
    out = tmp_path / "renders"

    result = studio_settings.update_settings({
        "tts_provider": "kokoro",
        "edge_tts_voice": " en-GB-SoniaNeural ",
        "kokoro_voice": "bf_emma",
        "kokoro_lang": "en-gb",
        "whisper_model": "small",
        "ollama_model": "gemma3:12b",
        "output_folder": str(out),
        "transition_pause": 1.5,
        "music_volume": 0.4,
        "slide_transition": "crossfade",
        "transition_duration": 1.2,
        "watermark_text": "  ACME Corp ",
        "watermark_position": "top-right",
        "watermark_opacity": 0.35,
    })

    # Stored under the keys the engine reads, normalised (the voice is stripped).
    assert config.tts_provider == "kokoro"
    assert config.edge_tts_voice == "en-GB-SoniaNeural"
    assert config.kokoro_voice == "bf_emma"
    assert config.kokoro_lang == "en-gb"
    assert config.whisper_model == "small"
    assert config._config["ollama_model"] == "gemma3:12b"
    assert config.output_folder == str(out)
    assert config.transition_pause == 1.5
    assert config.music_volume == 0.4
    assert config.slide_transition == "crossfade"
    assert config.transition_duration == 1.2
    assert config.watermark_text == "ACME Corp"
    assert config.watermark_position == "top-right"
    assert config.watermark_opacity == 0.35
    assert result == studio_settings.get_settings()
    assert saves == [1], "every change lands in one write of data/config.json"


def test_watermark_text_may_be_cleared():
    studio_settings.update_settings({"watermark_text": "Brand"})
    studio_settings.update_settings({"watermark_text": "   "})
    assert config.watermark_text == "", "empty = no watermark"


def test_partial_update_leaves_other_fields_alone():
    config._config["music_volume"] = 0.9
    studio_settings.update_settings({"transition_pause": 2})
    assert config.music_volume == 0.9
    assert config.transition_pause == 2.0


def test_empty_update_writes_nothing(monkeypatch):
    saves = []
    monkeypatch.setattr(config, "save", lambda: saves.append(1))
    assert studio_settings.update_settings({}) == studio_settings.get_settings()
    assert saves == []


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("tts_provider", "polly", "Unknown narration provider 'polly'"),
        ("tts_provider", "", "Unknown narration provider"),
        ("edge_tts_voice", "", "Enter an Edge TTS voice id"),
        ("edge_tts_voice", "af_heart", "looks like a Kokoro voice"),
        ("kokoro_voice", "   ", "Enter a Kokoro voice id"),
        ("kokoro_voice", "en-US-AriaNeural", "looks like an Edge TTS voice"),
        ("kokoro_lang", "klingon", "Unknown Kokoro language 'klingon'"),
        ("whisper_model", "huge", "Unknown Whisper model 'huge'"),
        ("whisper_model", 3, "Unknown Whisper model"),
        ("ollama_model", "", "Enter an Ollama model name"),
        ("output_folder", "", "Enter an output folder"),
        ("output_folder", "relative/renders", "must be an absolute path"),
        ("transition_pause", 5.5, "between 0 and 5"),
        ("transition_pause", -0.1, "between 0 and 5"),
        ("transition_pause", "slow", "between 0 and 5"),
        ("transition_pause", True, "between 0 and 5"),
        ("music_volume", 1.5, "between 0 (silent) and 1 (full)"),
        ("music_volume", float("nan"), "between 0 (silent) and 1 (full)"),
        ("music_volume", None, "between 0 (silent) and 1 (full)"),
        ("slide_transition", "wipe", "Unknown slide transition 'wipe'"),
        ("slide_transition", None, "Unknown slide transition"),
        ("transition_duration", 0.05, "between 0.1 and 2"),
        ("transition_duration", 2.5, "between 0.1 and 2"),
        ("transition_duration", "fast", "between 0.1 and 2"),
        ("watermark_text", 5, "must be text"),
        ("watermark_text", None, "must be text"),
        ("watermark_position", "middle", "Unknown watermark position 'middle'"),
        ("watermark_opacity", 0, "between 0.1 (faint) and 1 (solid)"),
        ("watermark_opacity", 1.5, "between 0.1 (faint) and 1 (solid)"),
        ("watermark_opacity", True, "between 0.1 (faint) and 1 (solid)"),
        ("brand_colour", "red", "Unknown setting: brand_colour"),
    ],
)
def test_update_rejects_bad_values_with_a_readable_message(field, value, expected):
    changes = {"music_volume": 0.5}  # a valid companion (replaced on the music_volume rows)
    changes[field] = value
    with pytest.raises(ValueError) as exc:
        studio_settings.update_settings(changes)
    assert expected in str(exc.value)
    # Refused as a whole: nothing in the request was written.
    assert config._config == {}


@pytest.mark.parametrize("value", ["", "tiny", "large-v3-turbo", "distil-large-v3", "large-v3"])
def test_whisper_model_accepts_the_engine_list_and_empty_for_default(value):
    studio_settings.update_settings({"whisper_model": value})
    assert config.whisper_model == value


def test_numbers_accept_the_bounds_and_numeric_strings():
    studio_settings.update_settings({"transition_pause": 5, "music_volume": "0"})
    assert config.transition_pause == 5.0
    assert config.music_volume == 0.0


def test_output_folder_is_created_and_stored_normalised(tmp_path):
    target = tmp_path / "studio" / "renders"
    assert not target.exists()
    studio_settings.update_settings({"output_folder": str(target)})
    assert target.is_dir()
    assert config.output_folder == str(target)
    assert not any(target.iterdir()), "the write probe is cleaned up"


def test_output_folder_that_cannot_be_created_is_rejected(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    with pytest.raises(ValueError) as exc:
        studio_settings.update_settings({"output_folder": str(blocker / "sub")})
    assert "could not be created" in str(exc.value)
    assert "output_folder" not in config._config


def test_output_folder_is_not_created_when_another_field_is_refused(tmp_path):
    """The filesystem work is a second pass: a request refused for any value
    leaves no folder behind."""
    target = tmp_path / "never-made"
    with pytest.raises(ValueError) as exc:
        studio_settings.update_settings({"output_folder": str(target), "music_volume": 3})
    assert "music volume" in str(exc.value)
    assert not target.exists()
    assert config._config == {}


def test_resolve_whisper_model_prefers_the_request_then_the_studio_setting():
    assert studio_settings.resolve_whisper_model(None) == ""  # "" = the engine's recommended default
    config._config["whisper_model"] = "small"
    assert studio_settings.resolve_whisper_model(None) == "small"
    assert studio_settings.resolve_whisper_model("") == "small"
    assert studio_settings.resolve_whisper_model("tiny") == "tiny"
    with pytest.raises(ValueError) as exc:
        studio_settings.resolve_whisper_model("huge")
    assert "Unknown Whisper model 'huge'" in str(exc.value)


def test_describe_lists_the_options_the_form_renders_from():
    d = studio_settings.describe()
    providers = {o["value"]: o for o in d["tts_provider"]["options"]}
    assert set(providers) == {"edge_tts", "kokoro"}
    assert providers["edge_tts"]["label"] == "Edge TTS"
    assert "downloads once" in providers["kokoro"]["note"]

    whisper = {o["value"]: o for o in d["whisper_model"]["options"]}
    assert "" in whisper and "Recommended" in whisper[""]["label"]
    assert {"tiny", "base", "small", "medium", "large-v3-turbo", "distil-large-v3", "large-v3"} <= set(whisper)
    assert "GPU" in whisper["large-v3-turbo"]["note"]
    assert "GPU recommended" in whisper["large-v3"]["note"]
    assert all(o["note"] for o in whisper.values())

    assert {o["value"] for o in d["kokoro_lang"]["options"]} == set(studio_settings.KOKORO_LANGUAGES)
    assert d["transition_pause"] == {"min": 0.0, "max": 5.0, "step": 0.1, "unit": "seconds"}
    assert d["music_volume"] == {"min": 0.0, "max": 1.0, "step": 0.05}

    transitions = d["slide_transition"]["options"]
    assert [o["value"] for o in transitions] == list(studio_settings.TRANSITIONS)
    assert transitions[0] == {"value": "none", "label": "None"}
    assert {o["label"] for o in transitions} >= {"Fade to Black", "Crossfade / Dissolve", "Zoom In"}
    assert d["transition_duration"] == {"min": 0.1, "max": 2.0, "step": 0.1, "unit": "seconds"}
    positions = d["watermark_position"]["options"]
    assert [o["value"] for o in positions] == ["top-left", "top-right", "bottom-left", "bottom-right", "center"]
    assert positions[-1]["label"] == "Center"
    assert d["watermark_opacity"] == {"min": 0.1, "max": 1.0, "step": 0.05}


def test_resolve_render_options_fills_nulls_from_the_studio():
    config._config.update({"slide_transition": "zoom-in", "transition_pause": 1.5, "watermark_text": "Brand"})
    out = studio_settings.resolve_render_options({
        "slide_transition": None, "transition_duration": 0.9, "watermark_text": "", "watermark_opacity": None,
        "intro_text": "ignored: not a studio default",
    })
    assert out == {
        "slide_transition": "zoom-in",  # null: the studio's
        "transition_duration": 0.9,  # given
        "transition_pause": 1.5,  # absent: the studio's
        "watermark_text": "",  # given, even when empty
        "watermark_position": "bottom-right",
        "watermark_opacity": 0.5,
    }
    assert set(out) == set(studio_settings.RENDER_OPTION_FIELDS)


def test_resolve_narration_fills_the_configured_defaults():
    config._config.update({"tts_provider": "kokoro", "kokoro_voice": "am_adam", "edge_tts_voice": "en-GB-RyanNeural"})
    assert studio_settings.resolve_narration(None, None) == ("kokoro", "am_adam")
    assert studio_settings.resolve_narration("", "") == ("kokoro", "am_adam")
    assert studio_settings.resolve_narration("edge_tts", None) == ("edge_tts", "en-GB-RyanNeural")
    assert studio_settings.resolve_narration(None, "af_sky") == ("kokoro", "af_sky")
    assert studio_settings.resolve_narration("edge_tts", " en-US-GuyNeural ") == ("edge_tts", "en-US-GuyNeural")


@pytest.mark.parametrize(
    "provider, voice, expected",
    [
        ("polly", "x", "Unknown narration provider 'polly'"),
        ("kokoro", "en-US-AriaNeural", "looks like an Edge TTS voice"),
        ("edge_tts", "af_heart", "looks like a Kokoro voice"),
    ],
)
def test_resolve_narration_refuses_a_voice_from_the_other_provider(provider, voice, expected):
    with pytest.raises(ValueError) as exc:
        studio_settings.resolve_narration(provider, voice)
    assert expected in str(exc.value)


def test_ollama_model_falls_back_to_the_revoice_default():
    assert studio_settings.ollama_model() == "llama3"
    config._config["ollama_model"] = "gemma3:12b"
    assert studio_settings.ollama_model() == "gemma3:12b"


# -- the API ---------------------------------------------------------------

@pytest.fixture
def app():
    from api.app import app as fastapi_app

    with TestClient(fastapi_app):
        # The seeded admin must change its password before it may call anything
        # but the auth routes (api/deps.py); keep "admin" as the password.
        admin = store.authenticate("admin", "admin")
        store.change_password(admin["id"], "admin", must_change=False)
        yield fastapi_app


def _login(app, username, password) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _editor(app, admin) -> TestClient:
    """A signed-in editor past the first-login password gate (until the new
    account changes its password, api/deps.py refuses it everything but the
    auth routes)."""
    r = admin.post("/api/users", json={"username": "ed", "password": "Editor-Pass-2026", "display_name": "Ed"})
    assert r.status_code == 201, r.text
    editor = _login(app, "ed", "Editor-Pass-2026")
    r = editor.post("/api/auth/change-password",
                    json={"current_password": "Editor-Pass-2026", "new_password": "Editor-Own-Pass-2026"})
    assert r.status_code == 200, r.text
    return editor


def test_every_signed_in_user_reads_the_studio_settings(app):
    admin = _login(app, "admin", "admin")
    r = admin.get("/api/settings/studio")
    assert r.status_code == 200
    assert r.json()["settings"] == studio_settings.get_settings()
    assert r.json()["options"]["tts_provider"]["options"][0]["value"] == "edge_tts"

    editor = _editor(app, admin)
    assert editor.get("/api/settings/studio").status_code == 200
    assert TestClient(app).get("/api/settings/studio").status_code == 401


def test_only_admins_change_the_studio_settings(app):
    admin = _login(app, "admin", "admin")
    editor = _editor(app, admin)
    assert editor.put("/api/settings/studio", json={"tts_provider": "kokoro"}).status_code == 403
    assert TestClient(app).put("/api/settings/studio", json={"tts_provider": "kokoro"}).status_code == 401
    assert "tts_provider" not in config._config


def test_admin_put_persists_a_partial_change(app):
    admin = _login(app, "admin", "admin")
    config._config["music_volume"] = 0.7

    r = admin.put("/api/settings/studio", json={"tts_provider": "kokoro", "transition_pause": 0.8, "whisper_model": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["tts_provider"] == "kokoro"
    assert body["settings"]["transition_pause"] == 0.8
    assert body["settings"]["music_volume"] == 0.7, "a field left out of the body is untouched"
    assert body["settings"]["whisper_model"] == "", "a null leaves the field alone"
    assert "options" in body
    assert config._config["tts_provider"] == "kokoro"
    assert config._config["transition_pause"] == 0.8

    assert admin.get("/api/settings/studio").json()["settings"] == body["settings"]


def test_put_refuses_a_bad_value_with_400_and_the_message(app):
    admin = _login(app, "admin", "admin")
    r = admin.put("/api/settings/studio", json={"music_volume": 3, "tts_provider": "kokoro"})
    assert r.status_code == 400
    assert "between 0 (silent) and 1 (full)" in r.json()["detail"]
    assert config._config == {}, "refused as a whole - nothing was written"


def test_admin_put_persists_the_generation_defaults(app):
    admin = _login(app, "admin", "admin")
    r = admin.put("/api/settings/studio", json={
        "slide_transition": "fade-to-white", "transition_duration": 0.8,
        "watermark_text": "ACME", "watermark_position": "center", "watermark_opacity": 0.3,
    })
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert (s["slide_transition"], s["transition_duration"]) == ("fade-to-white", 0.8)
    assert (s["watermark_text"], s["watermark_position"], s["watermark_opacity"]) == ("ACME", "center", 0.3)
    assert r.json()["options"]["slide_transition"]["options"][0]["value"] == "none"
    assert admin.put("/api/settings/studio", json={"slide_transition": "wipe"}).status_code == 400
    assert config.slide_transition == "fade-to-white"


def test_put_refuses_a_wrong_type_with_422(app):
    admin = _login(app, "admin", "admin")
    assert admin.put("/api/settings/studio", json={"transition_pause": "slow"}).status_code == 422
    assert admin.put("/api/settings/studio", json={"tts_provider": ["kokoro"]}).status_code == 422
    assert admin.put("/api/settings/studio", json={"slide_transition": 3}).status_code == 422
    assert admin.put("/api/settings/studio", json={"watermark_text": ["ACME"]}).status_code == 422
    # Strict numbers: a boolean is not 1.0 ...
    assert admin.put("/api/settings/studio", json={"transition_pause": True}).status_code == 422
    assert admin.put("/api/settings/studio", json={"music_volume": False}).status_code == 422
    assert admin.put("/api/settings/studio", json={"watermark_opacity": True}).status_code == 422
    assert admin.put("/api/settings/studio", json={"transition_duration": "1"}).status_code == 422
    assert config._config == {}
    # ... but an integer is a fine number.
    r = admin.put("/api/settings/studio", json={"transition_pause": 2})
    assert r.status_code == 200 and r.json()["settings"]["transition_pause"] == 2.0


def test_put_refuses_an_unknown_field_with_422(app):
    admin = _login(app, "admin", "admin")
    r = admin.put("/api/settings/studio", json={"brand_colour": "red", "music_volume": 0.5})
    assert r.status_code == 422
    assert "brand_colour" in r.text
    assert config._config == {}, "nothing in a refused request is written"
