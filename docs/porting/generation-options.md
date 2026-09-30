# Porting spec — generation options (vertical 2)

> **Status: 2a is DONE**, shipped in 0.3.0 (transitions, intro and outro cards, the text
> watermark, subtitle modes, the extra formats, the preview render, and the four engine
> defects that had to be fixed for any of it to work — see the `#generation-options` entry
> in CHANGELOG.md). **2b is NOT started**: the music and transition-sound library, section
> 4 below, is the only part of this spec still outstanding. Everything above it describes
> what shipped, so read it as a map of the code rather than as work to do. The edit
> timeline's music lane — E4 in `docs/porting/edit-timeline.md` §7 — depends on that same
> library, so 2b is built once, for the generate path and the lane both.

Engine-supported generation options that the React UI did not expose. Produced by
a read-only survey of the carried-over engine on 2026-09-14; every claim cites the code.
Vertical 2a = everything below except music/transition sounds; 2b = the media library.

## Current generate path

- `api/schemas.py` `GenerateRequest { voice_id, speed=1.0, preset="youtube_1080p" }`.
- `frontend/src/pages/ProjectDetail.tsx:140` posts those three; the card has three controls.
- `api/routers/projects.py:84-129` → `VideoProcessor(voice_id, resolution, speed, video_bitrate)`
  (only 4 of the 9 constructor params are passed) → `process_files([fi], output_dir, progress)`
  (`preview_seconds` never passed).
- `services/processing.py:207-367` `process_files` → `_build_video` (`:875`) → `VideoCreator`
  (`:976-995`) → `create_video` (`:1006`); then `_generate_srt` (`:347`, `:1034`) and
  `generate_extra_formats` (`:348`, `:1076`) run on every non-preview render.
- Structural fact: `_build_video` reads almost everything off the global `config` singleton
  (`utils/config.py`, `data/config.json`). Per-job options must become `VideoProcessor`
  constructor params forwarded at `processing.py:976-995`; otherwise two concurrent jobs
  (`services/jobs.py` has `max_workers=2`) race on global config.

## 1. Slide transitions

- `core/video_creator.py:453` `slide_transition` ∈ none, fade-to-black, fade-to-white,
  crossfade, slide-left, slide-right, slide-up, slide-down, zoom-in (default none;
  `utils/config.py:118`); `:454` `transition_duration` 0.1–2.0 s (default 0.5);
  `:444` `transition_pause` (DEFAULT_CONFIG says 0.0, `VideoCreator` default 1.0,
  SlideStudio hard-coded 0.8 at `gui/components/settings_tab.py:73-75`);
  `transition_sound_path` is already a `VideoProcessor` ctor param (`processing.py:180`).
- Effect code `:570-699`; gate `:961` (`slide_transition != "none" and duration > 0`).
- SlideStudio UI: `settings_tab.py:80-112` select with labels None / Fade to Black /
  Fade to White / Crossfade · Dissolve / Slide Left / Slide Right / Slide Up / Slide Down /
  Zoom In, plus a duration slider. Global config.
- TRAPS (fix, don't just wire): `fps=2` (`:443`, never overridden; ETA at
  `processing.py:943` assumes 2) makes a 0.5 s transition one frame — raise fps when a
  transition is on (cost: encode time) and fix the ETA; `:648-649` applies the horizontal
  slide transform TWICE (bug); `zoom-in` resizes with PIL per frame (slow at 4K);
  `assets/transitions/` does not exist (`services/styles.py:17` names it, nothing creates
  or serves it).

## 2. Intro / outro cards

- `core/video_creator.py:455-459` `intro_text, intro_subtitle, intro_duration=3.0,
  outro_text, outro_duration=3.0`; rendered by `_create_title_card` `:802-821`
  (black ColorClip + TextClip Arial 48 / 28); inserted `:974-986`; forwarded at
  `processing.py:989-993`; config props `utils/config.py:609-655`.
- SlideStudio UI: `settings_tab.py:611-665` expansion "Intro / Outro Title Cards":
  Title, Subtitle, intro duration 1–10; Closing text, outro duration 1–10.
- TRAPS: the outro takes NO subtitle (`:984` hard-codes ""); title cards have no audio and
  `create_video:1001-1010` attaches the master narration at t=0, so an intro card
  DESYNCHRONISES every slide by `intro_duration` — `_build_master_audio` (`:309`), the SRT
  (`processing.py:1034-1064`) and chapter markers (`_embed_chapters` `:823-892`) must all be
  offset; `TextClip(font="Arial")` failure is caught at `:819-820` and silently yields a
  black card.

## 3. Watermark

- `core/video_creator.py:449-452` `watermark_text, watermark_image, watermark_position`
  (top-left, top-right, bottom-left, bottom-right, center), `watermark_opacity=0.5`;
  `_apply_watermark` `:764-800`; applied `:1021-1026`; forwarded `processing.py:983-986`.
  Config keys absent from DEFAULT_CONFIG (property literals at `utils/config.py:549-576`).
- SlideStudio UI: `settings_tab.py:546-587` "Watermark / Branding": text, image path,
  position, opacity 0.1–1.0 shown as %.
- TRAPS: text wins over image (`:770/:778`); image needs a server-side path (no upload
  endpoint yet) — ship TEXT ONLY in 2a; font failure silently drops the watermark.

## 4. Background music (2b)

- `VideoProcessor(background_music_paths=[...])` `processing.py:179` (per-job, unpassed);
  `VideoCreator(background_music_paths, music_volume=0.25, music_fade_duration=2.0)`
  `core/video_creator.py:446-448`; `_apply_background_music` `:701-762` concatenates the
  playlist, loops/trims to length, linear volume multiplier, one fade in/out; no ducking.
  Re-voice path uses it too (`processing.py:850-868`).
- SlideStudio UI: per-session ordered playlist (`gui/state.py:33`), library folder
  `assets/music` (`gui/styles.py:27`), `gui/components/audio_tab.py` (playlist with
  preview/move/remove, Add File copies into the library, volume slider).
- TRAPS: `assets/music/` does not exist here and nothing serves it — needs a library API
  (upload, list, delete) with filename validation; non-existent paths are silently dropped
  (`:468`); volume > 1.0 clips.

## 5. Auto-subtitles

- (a) `services/processing.py:1034-1064` `_generate_srt` ALREADY runs on every generate:
  one cue per slide from `audio_duration` + `transition_pause`, written beside the MP4.
  Ignores `voice_start_delay` (drift) and the intro offset.
- (b) `core/subtitle_generator.py:112-219` `generate_subtitles(audio_path, output_dir,
  model_size, language, on_progress)` — faster-whisper word timestamps, 10 words per cue,
  writes `.srt` AND `.vtt` (`:205-210`); `burn_subtitles` `:222-283` (calls bare "ffmpeg",
  not `FFMPEG_PATH` — fix). Unreachable from Media Studio today. SlideStudio ran it as a
  post-generate "Subtitles" button (`gui/components/generation_export_actions.py:52-93`).
- TRAP: (b) transcribes the MP4 you just rendered (a second Whisper pass) and would
  OVERWRITE (a)'s `.srt` (same stem) — give the whisper output a distinct name or make the
  mode exclusive. Neither file is downloadable today (`GET /{pid}/video` serves only the MP4).

## 6. Extra export formats

- `services/processing.py:1076-1130` `generate_extra_formats`: `export_webm` (VP9,
  600 s timeout), `export_gif` (first 30 s, 300 s), `export_audio_only` (MP3, 300 s) read
  from `config._config` — must become explicit per-job arguments; failures are swallowed
  into log lines; nothing serves the outputs (needs download routes + `project.json` fields).
- SlideStudio UI: `settings_tab.py:591-607` three checkboxes.

## 7. Not generate options (separate endpoints)

- `voice_override` per slide is honoured by generate (`processing.py:576-580`) but has no
  writer — a slide-editor PATCH (vertical 3). `pause_override` is stored but read by
  nothing in the render path. `core/auto_pacing.py` rewrites notes (rules or Ollama) —
  a POST, not a generate field. `prepare_audio` (`processing.py:369-379`) has no route.

## 8. Other accepted parameters

- `preview_seconds` (`processing.py:212`) — budget-based partial render, output stem
  `_preview`, skips SRT/extra formats; SlideStudio's "Preview 15s" button. Never passed.
- `voice_start_delay` (`utils/config.py:125`, default mismatch 0.0 vs property fallback 1.0).
- `stability / similarity_boost / style` — per-job ctor params, unpassed (ElevenLabs-era).
- `fps` — never passed (see §1). `video_bitrate` — already wired via the preset.
- Slide range — does not exist anywhere; not a port.

## Recommended request schema (2a)

```python
class GenerateRequest(BaseModel):
    voice_id: str | None = None          # None = configured default for the provider
    provider: str | None = None          # vertical 1
    speed: float = Field(1.0, ge=0.5, le=2.0)
    preset: str = "youtube_1080p"
    slide_transition: Literal["none","fade-to-black","fade-to-white","crossfade",
                              "slide-left","slide-right","slide-up","slide-down","zoom-in"] = "none"
    transition_duration: float = Field(0.5, ge=0.1, le=2.0)
    transition_pause: float = Field(0.8, ge=0.0, le=5.0)
    intro_text: str = ""; intro_subtitle: str = ""; intro_duration: float = Field(3.0, ge=1.0, le=10.0)
    outro_text: str = ""; outro_duration: float = Field(3.0, ge=1.0, le=10.0)
    watermark_text: str = ""
    watermark_position: Literal["top-left","top-right","bottom-left","bottom-right","center"] = "bottom-right"
    watermark_opacity: float = Field(0.5, ge=0.1, le=1.0)
    subtitles: Literal["none","slide","whisper"] = "slide"
    export_webm: bool = False; export_gif: bool = False; export_audio_only: bool = False
    preview_seconds: float = Field(0, ge=0, le=120)
```
2b adds `transition_sound: str | None` and `music_tracks: list[str]` (library filenames,
validated like `_PID_RE` in `services/projects.py:27`) and `music_volume`.

Settings defaults (vertical 1's Studio settings feed the card's prefill): transition
defaults, watermark brand text/position/opacity, music volume, voice_start_delay, fps.
Per generation: everything in the schema, prefilled from those defaults.

## As built — Q1: the encode (2026-09-30)

Every video the app made was libx264 `ultrafast` with no profile and no audio bitrate. The owner chose
better renders at the cost of a slower final render; the rule was `medium` unless the whole render grows
more than 4×.

**What the render is given now.** `services/output_presets.py`: every preset gains `x264_preset`
(`"medium"`), `profile` (`"high"`) and `audio_bitrate` (`"192k"`, see below); the descriptions name
what the encoder is given ("1920×1080 for YouTube; H.264 High, AAC 192 kbps."; Vimeo's "at a 10 Mbps
target") and `tests/test_output_presets.py` holds each description to its fields. The generate route threads the three into `VideoProcessor`
(`x264_preset`, `h264_profile`, `audio_bitrate`), which threads them into `VideoCreator` as it does the
video bitrate. `VideoCreator.encode_settings()` is the one place `create_video` takes its
`write_videofile` arguments from: `preset=<x264_preset>`, `audio_bitrate=<audio_bitrate>`,
`ffmpeg_params=["-profile:v", <profile>, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]`. The preview
(`_build_video` with `preview_seconds > 0`) swaps only the x264 preset, for
`output_presets.PREVIEW_X264_PRESET` (`ultrafast`). `embed_chapters` passes `+faststart` too: its
stream-copy remux is the last write of every deck render with slide titles (and every re-voice with
markers), and a plain remux had put the index back at the end. The re-voice: `cut_picture` encodes at
`REVOICE_X264_PRESET` / `REVOICE_H264_PROFILE` (`medium`, High, 4:2:0) and passes the source's frame
rate as `-x264-params fps=<rate>` (`source_frame_rate`, read from ffmpeg's header); the re-voice master
is exported as a WAV (`services/processing.py`), and the standalone narration track separately as an MP3
at `NARRATION_MP3_BITRATE` (160k); `_build_replace_audio_cmd` still copies the picture (`-c:v copy`) and
its one audio chain is `mux_audio_filter`: `UPMIX_STEREO`, `RESAMPLE_48K`, then the bounded `apad`; it
gains `-b:a REVOICE_AUDIO_BITRATE` (192k, the music pass's number, one constant for both) and
`+faststart`; `music_filtergraph` resamples every input to 48 kHz after its up-mix.

**Measured before choosing** (the dev box, i9-9900K, bundled ffmpeg 7.1 for the encode and the remux;
deck `36bf22ddb6a9` — the rig has no deck of 5-15 slides — cut to its first ten slides in a scratch copy,
13,200 characters of notes, 837.97 s of Edge narration, 1080p, no transition, 2 fps; the narration
synthesised once beforehand, so no run synthesised anything):

| Run | Whole render | `write_videofile` | File | Video | Audio |
| --- | --- | --- | --- | --- | --- |
| `ultrafast` (HEAD `9661e8a`), twice | 199.05 s, 196.29 s | 182.69 s, 180.04 s | 32,682,515 B | Constrained Baseline, 185 kbit/s | AAC 125 kbit/s |
| `medium` (Q1), twice | 208.33 s, 209.13 s | 191.95 s, 193.20 s | 23,996,833 B | High, 41 kbit/s | AAC 186 kbit/s |

1.06× the whole render; the write step is 92 % of either, and it is mostly moviepy building frames and
the audio: x264 alone on the same 1,676 frames takes about 1.7 s at `ultrafast` and 4.3 s at `medium`.
Both files 4:2:0, ten chapters; `moov` after `mdat` before, in front now. Repeat runs were byte-identical.
The worst case, a crossfade at 24 fps (the first three slides, 138 s): 327.2 s against 328.6 s, 8.9 MB
against 5.5 MB. The 15-second preview (`ultrafast` + the parameters): 11.1 s, Constrained Baseline,
yuv420p, AAC 189 kbit/s, `moov` first. So `medium` ships.

**The re-voice's cut** on the 341 s 1080p30 corpus source (`d6df4cf2c1a6`, the spec's keep): 16.6 s and
22.2 s at `ultrafast` (68.9 MB), 32.1 s and 34.1 s at `medium` (18.6 MB). `cut_timeout` (60 s + 3 s per
second of reach) keeps some thirty times headroom. The Render button's estimate
(`frontend/src/lib/edit.ts`, `renderEstimate`) moves from `5 + reach × 0.05` to `5 + reach × 0.1`: the
corpus edit now reads "about 40 s" (was 25).

**Four things the build found.**

- **At the codec's default constant quality, `medium` buys size, not fidelity.** Both presets run at
  x264's default CRF, so each aims at the same quality: a rendered slide against its PNG measures SSIM
  0.99797 at `ultrafast` and 0.99787 at `medium`; the corpus cut, frame-aligned against the kept source
  frames (the Reviewer's measurement, `q1-review/corpus-cut-quality.log`), SSIM 0.99911 and PSNR
  56.5 dB at `ultrafast`, 0.99834 and 50.8 dB at `medium` - both visually transparent. (The first
  figures this note gave for the cut, 0.99816/27.3 dB and 0.99732/25.7 dB, were measured by timestamp
  over the first 47 s rather than frame against frame, did not reproduce and had no log; they are
  withdrawn.) The gain is
  a 4.5× smaller video stream on the deck, 3.7× on the cut, and a High stream. A visibly better picture
  would be a lower CRF, a separate decision the owner has not been asked.

- **The audio bitrate the brief named cannot be written by the render's encoder.** The render's AAC
  encoder is ffmpeg's own `aac`. Asked for 384k or 320k it writes about 210 kbit/s of the app's
  narration, about 257 kbit/s of full-band music and about 223 kbit/s of pink noise at 44.1 kHz stereo
  (about 240-280 at 48 kHz): it stops adding bits once its quantiser is at its finest. It tracks the
  request up to about 224k (215 of narration, 223 of music). So YouTube's 384 and Vimeo's 320 would have
  been labels for files the app never makes — the thing 0.11.0 fixed; planted at 384k, the real-binary
  test read 261k back. Every preset gives the encoder 192k: a narrated render reads at or a little
  under it and lower over silence (186 kbit/s over the 14-minute deck; 163-167 over the Reviewer's
  21 s decks; 148 over 25 s with two title cards), and dense content reaches it (music 192.7k, noise
  192.0k). The narration itself is 24 kHz mono (Edge: 48 kbit/s MP3). The bundled build (and 8.0.1)
  also carries Windows' MediaFoundation encoder, `aac_mf`, a constant-bitrate encoder with fixed steps:
  it writes 160, 192, 256 and 320k exactly (224k snaps to 256k, 384k down to 320k). An owner's decision
  is open: keep 192k; name 224k for YouTube and Vimeo on `aac` (the highest value still inside the ±15 %
  proof on narration); or move the render's audio to `aac_mf` for 320k (Vimeo's figure exactly, the
  most YouTube can get), at the price of a Windows-only encoder that Windows "N" editions lack without
  the Media Feature Pack.
- **`-profile:v` is a ceiling, not a label.** `ultrafast` with `-profile:v high` still writes
  Constrained Baseline (x264 flags the tools it uses); `medium` writes High with or without it. The
  preview therefore reads Constrained Baseline, which every High decoder plays.
- **moviepy 2.1.2 appends `-pix_fmt yuva420p` after `ffmpeg_params`**, and ffmpeg keeps the last one;
  libx264 has no yuva420p, so ffmpeg picks yuv420p itself. Our `-pix_fmt yuv420p` is overridden but the
  file is 4:2:0 either way; the real-binary test reads it back.

**The fix round (the review's M-1, m-1 to m-4, n-1 to n-3).** Measured on the bundled 7.1.

- **Every re-voice file carries its sound at 48 kHz now (M-1).** The review found every re-voice output
  at the TTS's 24 kHz - AAC-LC cannot carry 192k there (6144 bits a channel a frame is 144 kbit/s a
  channel) - behind a master that pydub exported at its default MP3 bitrate, 32 kbit/s for 24 kHz
  mono. The master is a WAV now (nothing downstream needs an MP3 but the narration track, which is
  exported on its own at 160k); the mux and the music pass up-mix at unity and resample to 48 kHz. The
  Reviewer's harness (`e2e_paths_test.py`: real Edge narration, all eight cut × music × markers paths)
  before and after:

  | Path | Before | After |
  | --- | --- | --- |
  | no cut, no music, no markers | 24 kHz mono, 97.1k | 48 kHz stereo, 181.6k |
  | no cut, no music, markers | 24 kHz mono, 99.1k | 48 kHz stereo, 184.4k |
  | no cut, music, no markers | 24 kHz stereo, 125.6k | 48 kHz stereo, 185.3k |
  | no cut, music, markers | 24 kHz stereo, 124.9k | 48 kHz stereo, 187.3k |
  | cut, no music, no markers | 24 kHz mono, 98.5k | 48 kHz stereo, 183.6k |
  | cut, no music, markers | 24 kHz mono, 98.7k | 48 kHz stereo, 185.0k |
  | cut, music, no markers | 24 kHz stereo, 132.0k | 48 kHz stereo, 192.8k |
  | cut, music, markers | 24 kHz stereo, 129.9k | 48 kHz stereo, 189.8k |

  All eight `moov` first and decoding clean. One short Edge sentence (5 s of a 48 kbit/s 24 kHz MP3)
  through each tree's own master export and mux onto a 6 s picture: before, a 32 kbit/s master and a
  24 kHz mono AAC at 87.2k, 17.8 dB SNR against the Edge original and 99 % of its energy below 5.9 kHz;
  after, a WAV master and a 48 kHz stereo AAC at 157.3k (the 1 s silent tail and the pauses bring it
  under 192), 45.8 dB SNR, 99 % below 6.75 kHz - the Edge original's own 6.76 kHz. The narration track:
  32k before, 160k after (57.3 dB SNR).
- **The cut's H.264 level (m-3).** On the bundled 7.1, `setpts` leaves the graph's frame rate unknown and
  `concat` hands on a 1/1000000 time base, so x264 took the cut for a million frames a second and declared
  Level 6.2 at any size (8.0.1 hands on the input's 1/15360 and declared 4.0 already, so the plant turns
  only the 7.1 runs red). Told the source's rate with `-x264-params fps=`, it declares what the picture needs: over the
  Reviewer's 40 s corpus window, Level 6.2 before and 4.0 after, 2,324,312 and 2,326,845 bytes, SSIM
  0.997815 and 0.997822, 1,200 frames both. `-r 30` gives the same level but forces a constant rate:
  on a variable-rate test source (30 fps then 15) it made 329 frames of 240, where `-x264-params fps=30`
  kept all 240 at their timestamps.
- **m-1:** every number in a description is what the encoder is given; the Vimeo bitrates are "a
  10 Mbps target" and "a 30 Mbps target" (the Reviewer measured 235 kbit/s for Vimeo 4K at 2 fps and
  463 kbit/s for Vimeo 1080p with a crossfade). **m-2:** the audio sentence says given 192k, read at or
  a little under it, lower over silence. **m-4:** faststart is read back from each re-voice writer's
  file. **n-2:** the CHANGELOG says the music pass already wrote faststart. **n-3:** a 4K30 cut at
  `medium` runs at 0.50 s a second of reach, six times inside `cut_timeout`.

**The proof.** `tests/test_encode_settings.py`: a one-slide synthetic deck through `create_video` itself
on every real ffmpeg (8.0.1 on the dev box and the installed 7.1), read back with ffprobe — High,
yuv420p, AAC within 15 % of the preset's, `moov` before `mdat` — once per distinct preset encode, once
through the chapter remux, once as the preview; a real re-voice job through the route on each binary -
the mux alone, the cut and the music pass, and all three with the chapter remux - read back for 48 kHz
stereo AAC between 85 % and 105 % of 192k over dense narration, the voice within 1 dB of its own track
(the unity up-mix), `moov` first and the cut's level within what its size and rate need; plus the argv
of the cut, the mux and the preview.
