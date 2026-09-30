"""Tests for the output presets registry (the encode per delivery target)."""

import re

from services import output_presets

# How a description spells each H.264 profile the presets may carry.
PROFILE_NAMES = {"baseline": "Baseline", "main": "Main", "high": "High"}


def test_list_presets_includes_every_target():
    ids = {p["id"] for p in output_presets.list_presets()}
    assert {
        "youtube_1080p", "youtube_4k", "linkedin_teams",
        "zoom_webinar", "vimeo_1080p", "vimeo_4k",
    } <= ids


def test_every_preset_has_the_expected_shape():
    for p in output_presets.list_presets():
        assert {
            "id", "label", "resolution", "video_bitrate", "x264_preset", "profile", "audio_bitrate", "description",
        } <= set(p), p["id"]
        assert len(p["resolution"]) == 2
        assert all(isinstance(n, int) for n in p["resolution"])
        assert isinstance(p["video_bitrate"], str)
        assert re.fullmatch(r"\d+k", p["audio_bitrate"]), (p["id"], p["audio_bitrate"])
        assert p["profile"] in PROFILE_NAMES, p["id"]


def test_get_preset_returns_the_matching_entry():
    assert output_presets.get_preset("vimeo_4k")["resolution"] == [3840, 2160]


def test_get_preset_defaults_to_youtube_1080p_when_unknown():
    assert output_presets.get_preset("does-not-exist")["id"] == "youtube_1080p"


def test_vimeo_presets_carry_an_explicit_bitrate():
    assert output_presets.get_preset("vimeo_1080p")["video_bitrate"] == "10M"
    assert output_presets.get_preset("vimeo_4k")["video_bitrate"] == "30M"


def test_streaming_presets_leave_the_bitrate_to_the_codec():
    assert output_presets.get_preset("youtube_1080p")["video_bitrate"] == ""
    assert output_presets.get_preset("zoom_webinar")["video_bitrate"] == ""


def test_every_final_render_is_x264_medium_h264_high_and_the_preview_stays_ultrafast():
    """Q1, the owner's choice: better renders for a slower final render.
    ``ultrafast`` turned off CABAC, B-frames and the 8×8 transform, and x264
    flagged its stream Constrained Baseline whatever profile was asked for;
    ``medium`` writes High. The 15-second preview is a look, not a
    deliverable, and keeps the fast encode."""
    for p in output_presets.list_presets():
        assert (p["x264_preset"], p["profile"]) == ("medium", "high"), p["id"]
    assert output_presets.PREVIEW_X264_PRESET == "ultrafast"


def test_every_preset_names_the_audio_bitrate_the_encoder_delivers():
    """The render's AAC encoder, ffmpeg's own ``aac``, stops adding bits at its
    finest quantiser: given 384k or 320k - YouTube's and Vimeo's published
    figures - it writes about 210 kbit/s of the app's narration and about 257
    of music (measured on the bundled 7.1), so a preset naming either would
    describe a file the app never makes. Given 192k, a narrated render reads
    at or a little under it, lower over silence. ``tests/test_encode_settings.py``
    measures each preset's bitrate on a real render (planted at 384k, it read
    261k back); this pins the number that measurement was made for."""
    for p in output_presets.list_presets():
        assert p["audio_bitrate"] == "192k", p["id"]


def test_every_description_says_what_the_render_is_given():
    """Each description names exactly what the encoder is given - one policy
    for every number in it: the resolution; the profile, spelled as H.264
    spells it; the video bitrate exactly when one is passed, and as the
    TARGET it is ("at a 10 Mbps target": a deck's repeated frames read far
    less); and the AAC bitrate - and no other bitrate. The Vimeo presets once
    promised "H.264 High ... AAC 320k" when the render passed neither; 0.11.0
    took the promise out, and Q1 put the encode in and the words back, held
    to the fields."""
    for p in output_presets.list_presets():
        text = p["description"]
        width, height = p["resolution"]
        assert f"{width}×{height}" in text, (p["id"], text)

        profile = PROFILE_NAMES[p["profile"]]
        assert f"H.264 {profile}" in text, (p["id"], text)
        for other in set(PROFILE_NAMES.values()) - {profile}:
            assert other not in text, f"{p['id']}: {text!r} names {other!r}, which the render does not pass"

        if p["video_bitrate"]:
            assert p["video_bitrate"].endswith("M"), p["id"]
            assert re.findall(r"(\d+) Mbps", text) == [p["video_bitrate"][:-1]], (p["id"], text)
            assert f"a {p['video_bitrate'][:-1]} Mbps target" in text, (p["id"], text)
        else:
            assert "Mbps" not in text, (p["id"], text)

        assert f"AAC {p['audio_bitrate'][:-1]} kbps" in text, (p["id"], text)
        assert re.findall(r"(\d+) kbps", text) == [p["audio_bitrate"][:-1]], (p["id"], text)
        assert "default quality" not in text, (p["id"], text)
