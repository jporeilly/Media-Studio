# Output presets

The **Output preset** on the Generate card sets the rendered MP4's resolution and, for the Vimeo presets, its video bitrate.

A deck or PDF render is H.264 video with AAC audio in an MP4.

| Preset | Resolution | Video bitrate | Description |
| --- | --- | --- | --- |
| YouTube 1080p (`youtube_1080p`) | 1920 × 1080 | codec default | 1920×1080 for YouTube; the encoder's default quality. |
| YouTube 4K (`youtube_4k`) | 3840 × 2160 | codec default | 3840×2160 (4K) for YouTube; the encoder's default quality. |
| LinkedIn / Teams (`linkedin_teams`) | 1920 × 1080 | codec default | 1920×1080 for LinkedIn and Microsoft Teams; the encoder's default quality. |
| Zoom webinar (`zoom_webinar`) | 1280 × 720 | codec default | 1280×720, a lighter file for Zoom webinars; the encoder's default quality. |
| Vimeo 1080p (`vimeo_1080p`) | 1920 × 1080 | 10 Mbit/s | 1920×1080 for Vimeo; video at 10 Mbps. |
| Vimeo 4K (`vimeo_4k`) | 3840 × 2160 | 30 Mbit/s | 3840×2160 (4K) for Vimeo; video at 30 Mbps. |

The descriptions are shown under the preset on the Generate card, and each says what the render is given: the resolution and, for Vimeo, the video bitrate. Every deck render is libx264 `ultrafast` with AAC at the encoder's default bitrate; no H.264 profile and no audio bitrate are passed.

The default is YouTube 1080p, and an unknown preset id falls back to it. "Codec default" means no bitrate is passed to the encoder and its own constant-quality setting applies; the Vimeo presets name a bitrate because Vimeo re-encodes every upload and a higher source bitrate survives that pass with visibly better quality.

## Frame rate

A deck with no transition effect is encoded at 2 frames a second: every frame is the same slide, so the encode stays fast. Any transition effect but None raises the render to 24 frames a second so the effect has frames to play on, which makes the encode take longer.

## Where the presets are used

The Generate card's render and its 15-second preview use the chosen preset. A re-voice copies the source video's picture untouched; when the Timeline's edit cuts the picture, the cut is re-encoded at the default preset's bitrate (the codec default) at the source's own frame rate.

## See also

- [Generate a video](../guides/generate-video.md)
- [Re-voicing a video](../guides/re-voice.md)
