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


def test_every_description_says_what_the_render_does_and_nothing_more():
    """The render passes the encoder the resolution and, where the preset has
    one, the video bitrate - and nothing else: no H.264 profile, no audio
    bitrate (``core.video_creator``'s ``write_videofile``: libx264
    ``ultrafast``, AAC at its default). So each description names its
    resolution, names its bitrate exactly when one is passed, and names no
    profile and no audio bitrate. The Vimeo presets used to promise "H.264
    High ... AAC 320k", which the file never had."""
    for p in output_presets.list_presets():
        text = p["description"]
        width, height = p["resolution"]
        assert f"{width}×{height}" in text, (p["id"], text)
        if p["video_bitrate"]:
            assert p["video_bitrate"].endswith("M"), p["id"]
            assert f"{p['video_bitrate'][:-1]} Mbps" in text, (p["id"], text)
        else:
            assert "bps" not in text and "bit" not in text, (p["id"], text)
            assert "default quality" in text, (p["id"], text)
        for claim in ("High", "Main", "Baseline", "profile", "AAC", "320k", "kbps", "audio"):
            assert claim not in text, f"{p['id']}: {text!r} names {claim!r}, which the render does not pass"


def test_the_render_passes_no_profile_and_no_audio_bitrate():
    """The other half of the test above: if the render starts passing a
    profile or an audio bitrate, the descriptions may say so - and this
    fails to make someone look at them."""
    import inspect

    from core import video_creator

    source = inspect.getsource(video_creator.VideoCreator)
    call = source[source.index("final_video.write_videofile("):]
    call = call[:call.index(")\n")]
    assert "audio_bitrate" not in call and "profile" not in call and "ffmpeg_params" not in call, call
    assert 'preset="ultrafast"' in call and "bitrate=self.video_bitrate or None" in call, call
