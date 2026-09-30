# Output presets

The **Output preset** on the Generate card sets the rendered MP4's resolution, its encode and, for the Vimeo presets, its video bitrate.

A deck or PDF render is H.264 video with AAC audio in an MP4.

| Preset | Resolution | Video | Audio | Description |
| --- | --- | --- | --- | --- |
| YouTube 1080p (`youtube_1080p`) | 1920 × 1080 | H.264 High, codec default | AAC 192 kbit/s | 1920×1080 for YouTube; H.264 High, AAC 192 kbps. |
| YouTube 4K (`youtube_4k`) | 3840 × 2160 | H.264 High, codec default | AAC 192 kbit/s | 3840×2160 (4K) for YouTube; H.264 High, AAC 192 kbps. |
| LinkedIn / Teams (`linkedin_teams`) | 1920 × 1080 | H.264 High, codec default | AAC 192 kbit/s | 1920×1080 for LinkedIn and Microsoft Teams; H.264 High, AAC 192 kbps. |
| Zoom webinar (`zoom_webinar`) | 1280 × 720 | H.264 High, codec default | AAC 192 kbit/s | 1280×720, a lighter file for Zoom webinars; H.264 High, AAC 192 kbps. |
| Vimeo 1080p (`vimeo_1080p`) | 1920 × 1080 | H.264 High, a 10 Mbit/s target | AAC 192 kbit/s | 1920×1080 for Vimeo; H.264 High at a 10 Mbps target, AAC 192 kbps. |
| Vimeo 4K (`vimeo_4k`) | 3840 × 2160 | H.264 High, a 30 Mbit/s target | AAC 192 kbit/s | 3840×2160 (4K) for Vimeo; H.264 High at a 30 Mbps target, AAC 192 kbps. |

The descriptions are shown under the preset on the Generate card, and every number in them is what the encoder is given. Every preset passes the encoder the same five things besides its resolution: libx264 at its `medium` setting, the H.264 High profile, 4:2:0 colour (`yuv420p`, what every browser, phone and TV plays), the index at the front of the file (`+faststart`, so a web upload starts playing before it has all arrived), and AAC given 192 kbit/s; the Vimeo presets add their video bitrate target. The file reads back as H.264 High and `yuv420p`, with its audio at or a little under 192 kbit/s where the narration is dense and lower over silence (pauses, the voice's start delay, the title cards): 186 kbit/s over a 14-minute deck, 148 kbit/s over a 25-second one with two title cards.

The default is YouTube 1080p, and an unknown preset id falls back to it. "Codec default" means no video bitrate is passed to the encoder and its own constant-quality setting applies; the Vimeo presets give the encoder a bitrate target because Vimeo re-encodes every upload and a higher source bitrate survives that pass with visibly better quality. A target is not what the file reads: a deck repeats the same slide frame after frame, which costs almost nothing to encode, so a Vimeo render reads far below its target (a Vimeo 4K deck at 2 frames a second reads about 235 kbit/s; a Vimeo 1080p deck with a crossfade at 24, about 463 kbit/s).

**The audio bitrate.** YouTube publishes 384 kbit/s for stereo AAC and Vimeo 320 kbit/s, but the app's AAC encoder (ffmpeg's own, the one in the build the app ships) does not write either: given 384 or 320 it writes about 210 kbit/s of narration and about 257 kbit/s of full-band music, because it stops adding bits once its quality is at its finest. Every preset therefore gives it 192 kbit/s, which it reaches on dense narration and music alike, and which a re-voice is given too. The narration itself is 24 kHz mono speech from either engine, so 192 kbit/s loses nothing of it.

**The preview.** **Preview 15 s** uses the chosen preset with one difference: libx264's `ultrafast` setting instead of `medium`, so the preview stays quick. It keeps the profile, 4:2:0, the index at the front and the audio bitrate, so it plays wherever the final render does. `ultrafast` uses none of the High profile's tools, so its stream reads back as Constrained Baseline, which every H.264 player plays.

## Frame rate

A deck with no transition effect is encoded at 2 frames a second: every frame is the same slide, so the encode stays fast. Any transition effect but None raises the render to 24 frames a second so the effect has frames to play on, which makes the encode take longer.

## Where the presets are used

The Generate card's render and its 15-second preview use the chosen preset. A re-voice copies the source video's picture untouched; when the Timeline's edit cuts the picture, the cut is re-encoded like a final render (libx264 `medium`, H.264 High, 4:2:0) at the default preset's bitrate (the codec default) and the source's own frame rate, which the encoder is told, so the H.264 level the file declares fits its size and rate (4.0 for 1080p at 30). A re-voice's narration, with or without music, is AAC at 48 kHz stereo given 192 kbit/s, and reads at or a little under it where the narration is dense, lower over silence.

## See also

- [Generate a video](../guides/generate-video.md)
- [Re-voicing a video](../guides/re-voice.md)
