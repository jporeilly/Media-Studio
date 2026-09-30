"""Output presets — the encode each delivery target gets for the final MP4.

One place that maps a user-facing preset id (a delivery target such as YouTube
or Vimeo) to the encode settings the engine needs, every one of them passed
through to ``write_videofile`` (``core.video_creator``,
``VideoCreator.encode_settings``): the render resolution, the H.264 video
bitrate, the x264 preset, the H.264 profile and the AAC audio bitrate. The
Vimeo presets carry an explicit video bitrate because Vimeo re-encodes every
upload and a higher source bitrate survives that pass with visibly better
quality; the streaming presets leave it empty so the codec's own
constant-quality default applies.

**A final render is x264 ``medium``, H.264 High** (Q1: the owner chose better
renders at the cost of a slower final render). Every render used to be
``ultrafast``, which turns off CABAC, B-frames and the 8×8 transform, so its
file was several times larger for the same quality - and x264 flagged it
Constrained Baseline whatever profile was asked for: ``-profile:v`` is a
ceiling on the tools x264 may use, not a label, and ``ultrafast`` uses none of
High's. ``medium`` uses them, so the stream is High. Both run at the codec's
constant-quality default, so the picture's fidelity is the same to within a
hair (a rendered slide against its image: SSIM 0.9980 at ``ultrafast``,
0.9979 at ``medium``) and the gain is the size. The cost was measured on a
real deck before choosing (the rule: ``medium`` unless the whole render grows
more than 4×): ten slides, 14 minutes of narration, 1080p, no transition - the
whole render 197.7 s at ``ultrafast`` and 208.7 s at ``medium`` (1.06×; most
of either is moviepy building the frames, not x264), the video stream
185 kbit/s and 41 kbit/s. The details are in
``docs/porting/generation-options.md`` (*As built — Q1*).

**Every number in a description is what the encoder is GIVEN** - one policy
for all of them, because the file reads back less than it was given wherever
the content needs less:

- The video bitrate is a target. The Vimeo presets give x264 10 or 30 Mbps,
  and a deck's slides are the same frame again and again, so the file reads
  far less (measured: Vimeo 4K at 2 fps, 235 kbit/s; Vimeo 1080p with a
  crossfade at 24 fps, 463 kbit/s). The descriptions say "a 10 Mbps target".
- The audio is given 192 kbit/s, and a narrated render reads at or a little
  under it, lower over silence (pauses, the voice-start delay, title cards):
  measured 186 kbit/s over a 14-minute deck, 148 kbit/s over a short one with
  two title cards. 192k and not a platform's figure, because ffmpeg's own
  ``aac`` - the render's encoder - stops adding bits once its quantiser is at
  its finest: given 384k (YouTube's) or 320k (Vimeo's) it writes about
  210 kbit/s of the app's narration and about 257 of full-band music at
  44.1 kHz, so either figure would describe a file the app never makes. It is
  the re-voice's number too (``core.video_creator.REVOICE_AUDIO_BITRATE``).
  (The bundled build also has Windows' MediaFoundation encoder, ``aac_mf``,
  which writes 320k exactly and snaps 384k down to 320k; moving the render to
  it is an owner's decision, not taken here.)

**A description says what the render is given and nothing more**: the
resolution, the profile, the video bitrate's target where one is passed, and
the audio bitrate. ``tests/test_output_presets.py`` holds every description
to the fields, and ``tests/test_encode_settings.py`` holds the fields to the
file, read back with ffprobe from a real render.
"""

DEFAULT_PRESET_ID = "youtube_1080p"

# The 15-second preview's x264 preset. A preview is a look at the opening
# seconds, not a deliverable, so it keeps the fast encode every render used to
# have; it still gets the profile, 4:2:0 and faststart parameters (and the
# preset's audio bitrate), so it plays everywhere the final render does.
PREVIEW_X264_PRESET = "ultrafast"

# Keyed by id. ``resolution`` is [w, h]; ``video_bitrate`` is an ffmpeg bitrate
# string (e.g. "10M") or "" to let the codec pick (quality-based, the default);
# ``x264_preset`` and ``profile`` are what libx264 is given; ``audio_bitrate``
# is the AAC bitrate, an ffmpeg bitrate string.
OUTPUT_PRESETS: dict[str, dict] = {
    "youtube_1080p": {
        "id": "youtube_1080p",
        "label": "YouTube 1080p",
        "resolution": [1920, 1080],
        "video_bitrate": "",
        "x264_preset": "medium",
        "profile": "high",
        "audio_bitrate": "192k",
        "description": "1920×1080 for YouTube; H.264 High, AAC 192 kbps.",
    },
    "youtube_4k": {
        "id": "youtube_4k",
        "label": "YouTube 4K",
        "resolution": [3840, 2160],
        "video_bitrate": "",
        "x264_preset": "medium",
        "profile": "high",
        "audio_bitrate": "192k",
        "description": "3840×2160 (4K) for YouTube; H.264 High, AAC 192 kbps.",
    },
    "linkedin_teams": {
        "id": "linkedin_teams",
        "label": "LinkedIn / Teams",
        "resolution": [1920, 1080],
        "video_bitrate": "",
        "x264_preset": "medium",
        "profile": "high",
        "audio_bitrate": "192k",
        "description": "1920×1080 for LinkedIn and Microsoft Teams; H.264 High, AAC 192 kbps.",
    },
    "zoom_webinar": {
        "id": "zoom_webinar",
        "label": "Zoom webinar",
        "resolution": [1280, 720],
        "video_bitrate": "",
        "x264_preset": "medium",
        "profile": "high",
        "audio_bitrate": "192k",
        "description": "1280×720, a lighter file for Zoom webinars; H.264 High, AAC 192 kbps.",
    },
    "vimeo_1080p": {
        "id": "vimeo_1080p",
        "label": "Vimeo 1080p",
        "resolution": [1920, 1080],
        "video_bitrate": "10M",
        "x264_preset": "medium",
        "profile": "high",
        "audio_bitrate": "192k",
        "description": "1920×1080 for Vimeo; H.264 High at a 10 Mbps target, AAC 192 kbps.",
    },
    "vimeo_4k": {
        "id": "vimeo_4k",
        "label": "Vimeo 4K",
        "resolution": [3840, 2160],
        "video_bitrate": "30M",
        "x264_preset": "medium",
        "profile": "high",
        "audio_bitrate": "192k",
        "description": "3840×2160 (4K) for Vimeo; H.264 High at a 30 Mbps target, AAC 192 kbps.",
    },
}


def list_presets() -> list[dict]:
    """All output presets, in declaration order."""
    return list(OUTPUT_PRESETS.values())


def get_preset(preset_id: str) -> dict | None:
    """The preset for ``preset_id``, defaulting to youtube_1080p when unknown."""
    return OUTPUT_PRESETS.get(preset_id) or OUTPUT_PRESETS[DEFAULT_PRESET_ID]
