"""Output presets — target-tuned resolution and bitrate for the final MP4.

One place that maps a user-facing preset id (a delivery target such as YouTube
or Vimeo) to the encode settings the engine needs: the render resolution and the
H.264 video bitrate passed through to ``write_videofile``. The Vimeo presets
carry an explicit bitrate because Vimeo re-encodes every upload and a higher
source bitrate survives that pass with visibly better quality; the streaming
presets leave the bitrate empty so the codec's own constant-quality default
applies (matching the pre-existing behaviour).

**A description says what the render does and nothing more**: the
resolution, and the video bitrate where one is passed. The deck render is
``write_videofile(codec="libx264", audio_codec="aac", preset="ultrafast",
bitrate=<video_bitrate or None>)`` (``core.video_creator``): no H.264 profile
and no audio bitrate reach the encoder, so no description may name one (they
used to say "H.264 High ... AAC 320k", Vimeo's recommendation rather than the
file). ``tests/test_output_presets.py`` holds every description to that.
"""

DEFAULT_PRESET_ID = "youtube_1080p"

# Keyed by id. ``resolution`` is [w, h]; ``video_bitrate`` is an ffmpeg bitrate
# string (e.g. "10M") or "" to let the codec pick (quality-based, the default).
OUTPUT_PRESETS: dict[str, dict] = {
    "youtube_1080p": {
        "id": "youtube_1080p",
        "label": "YouTube 1080p",
        "resolution": [1920, 1080],
        "video_bitrate": "",
        "description": "1920×1080 for YouTube; the encoder's default quality.",
    },
    "youtube_4k": {
        "id": "youtube_4k",
        "label": "YouTube 4K",
        "resolution": [3840, 2160],
        "video_bitrate": "",
        "description": "3840×2160 (4K) for YouTube; the encoder's default quality.",
    },
    "linkedin_teams": {
        "id": "linkedin_teams",
        "label": "LinkedIn / Teams",
        "resolution": [1920, 1080],
        "video_bitrate": "",
        "description": "1920×1080 for LinkedIn and Microsoft Teams; the encoder's default quality.",
    },
    "zoom_webinar": {
        "id": "zoom_webinar",
        "label": "Zoom webinar",
        "resolution": [1280, 720],
        "video_bitrate": "",
        "description": "1280×720, a lighter file for Zoom webinars; the encoder's default quality.",
    },
    "vimeo_1080p": {
        "id": "vimeo_1080p",
        "label": "Vimeo 1080p",
        "resolution": [1920, 1080],
        "video_bitrate": "10M",
        "description": "1920×1080 for Vimeo; video at 10 Mbps.",
    },
    "vimeo_4k": {
        "id": "vimeo_4k",
        "label": "Vimeo 4K",
        "resolution": [3840, 2160],
        "video_bitrate": "30M",
        "description": "3840×2160 (4K) for Vimeo; video at 30 Mbps.",
    },
}


def list_presets() -> list[dict]:
    """All output presets, in declaration order."""
    return list(OUTPUT_PRESETS.values())


def get_preset(preset_id: str) -> dict | None:
    """The preset for ``preset_id``, defaulting to youtube_1080p when unknown."""
    return OUTPUT_PRESETS.get(preset_id) or OUTPUT_PRESETS[DEFAULT_PRESET_ID]
