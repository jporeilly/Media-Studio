"""Tests for the output presets registry (resolution + bitrate per target)."""

from services import output_presets


def test_list_presets_includes_every_target():
    ids = {p["id"] for p in output_presets.list_presets()}
    assert {
        "youtube_1080p", "youtube_4k", "linkedin_teams",
        "zoom_webinar", "vimeo_1080p", "vimeo_4k",
    } <= ids


def test_every_preset_has_the_expected_shape():
    for p in output_presets.list_presets():
        assert {"id", "label", "resolution", "video_bitrate", "description"} <= set(p)
        assert len(p["resolution"]) == 2
        assert all(isinstance(n, int) for n in p["resolution"])
        assert isinstance(p["video_bitrate"], str)


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
