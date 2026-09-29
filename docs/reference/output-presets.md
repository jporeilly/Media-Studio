# Output presets

The **Output preset** on the Generate card sets the rendered MP4's resolution and, for the Vimeo presets, its video bitrate.

A deck or PDF render is H.264 video with AAC audio in an MP4.

| Preset | Resolution | Video bitrate | Description |
| --- | --- | --- | --- |
| YouTube 1080p (`youtube_1080p`) | 1920 × 1080 | codec default | 1080p for YouTube; codec-default quality. |
| YouTube 4K (`youtube_4k`) | 3840 × 2160 | codec default | 2160p (4K) for YouTube; codec-default quality. |
| LinkedIn / Teams (`linkedin_teams`) | 1920 × 1080 | codec default | 1080p for LinkedIn and Microsoft Teams. |
| Zoom webinar (`zoom_webinar`) | 1280 × 720 | codec default | 720p, a lighter file for Zoom webinars. |
| Vimeo 1080p (`vimeo_1080p`) | 1920 × 1080 | 10 Mbit/s | Vimeo recommended: H.264 High, ~10 Mbps, AAC 320k |
| Vimeo 4K (`vimeo_4k`) | 3840 × 2160 | 30 Mbit/s | Vimeo recommended 4K: H.264 High, ~30 Mbps, AAC 320k |

The descriptions are the app's own labels, shown under the preset on the Generate card. The encoder is given only the resolution and, for Vimeo, the video bitrate: every deck render is libx264 `ultrafast` with AAC at the encoder's default bitrate. No profile and no audio bitrate are passed, so the "H.264 High" and "AAC 320k" in the Vimeo labels are Vimeo's recommendation, not a description of the file.

The default is YouTube 1080p, and an unknown preset id falls back to it. "Codec default" means no bitrate is passed to the encoder and its own constant-quality setting applies; the Vimeo presets name a bitrate because Vimeo re-encodes every upload and a higher source bitrate survives that pass with visibly better quality.

## Frame rate

A deck with no transition effect is encoded at 2 frames a second: every frame is the same slide, so the encode stays fast. Any transition effect but None raises the render to 24 frames a second so the effect has frames to play on, which makes the encode take longer.

## Where the presets are used

The Generate card's render and its 15-second preview use the chosen preset. A re-voice copies the source video's picture untouched; when the Timeline's edit cuts the picture, the cut is re-encoded at the default preset's bitrate (the codec default) at the source's own frame rate.

## See also

- [Generate a video](../guides/generate-video.md)
- [Re-voicing a video](../guides/re-voice.md)
