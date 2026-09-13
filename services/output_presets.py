"""Output presets — target-tuned resolution and bitrate for the final MP4.

One place that maps a user-facing preset id (a delivery target such as YouTube
or Vimeo) to the encode settings the engine needs: the render resolution and the
H.264 video bitrate passed through to ``write_videofile``. The Vimeo presets
carry an explicit bitrate because Vimeo re-encodes every upload and a higher
source bitrate survives that pass with visibly better quality; the streaming
presets leave the bitrate empty so the codec's own constant-quality default
applies (matching the pre-existing behaviour).
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
        "description": "1080p for YouTube; codec-default quality.",
    },
    "youtube_4k": {
        "id": "youtube_4k",
        "label": "YouTube 4K",
        "resolution": [3840, 2160],
        "video_bitrate": "",
        "description": "2160p (4K) for YouTube; codec-default quality.",
    },
    "linkedin_teams": {
        "id": "linkedin_teams",
        "label": "LinkedIn / Teams",
        "resolution": [1920, 1080],
        "video_bitrate": "",
        "description": "1080p for LinkedIn and Microsoft Teams.",
    },
    "zoom_webinar": {
        "id": "zoom_webinar",
        "label": "Zoom webinar",
        "resolution": [1280, 720],
        "video_bitrate": "",
        "description": "720p, a lighter file for Zoom webinars.",
    },
    "vimeo_1080p": {
        "id": "vimeo_1080p",
        "label": "Vimeo 1080p",
        "resolution": [1920, 1080],
        "video_bitrate": "10M",
        "description": "Vimeo recommended: H.264 High, ~10 Mbps, AAC 320k",
    },
    "vimeo_4k": {
        "id": "vimeo_4k",
        "label": "Vimeo 4K",
        "resolution": [3840, 2160],
        "video_bitrate": "30M",
        "description": "Vimeo recommended 4K: H.264 High, ~30 Mbps, AAC 320k",
    },
}


def list_presets() -> list[dict]:
    """All output presets, in declaration order."""
    return list(OUTPUT_PRESETS.values())


def get_preset(preset_id: str) -> dict | None:
    """The preset for ``preset_id``, defaulting to youtube_1080p when unknown."""
    return OUTPUT_PRESETS.get(preset_id) or OUTPUT_PRESETS[DEFAULT_PRESET_ID]
