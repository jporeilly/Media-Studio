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

## As built — T1: ffmpeg composes the deck's picture (2026-10-02)

On 0.11.0 the owner's 20-slide deck (`pdi-hello-world`, 443 s of Edge narration, 1080p) took 105.0 s to render with no transition and 2,522.2 s (42 minutes) with a transition and the text watermark. moviepy built every frame in numpy and piped raw RGB to ffmpeg; a transition raised the whole video from 2 to 24 frames a second; and the watermark was composited onto each of its frames (10,704 by its log, at 24 a second). The brief is `t1-brief.md`, the owner's decisions after the interim report are `t1-decisions.md`, and the review that led to the fix round below is `t1-review.md`. The decisions:

- fade-to-white is fixed;
- the slide directions are fixed;
- animated slides are fixed;
- the narration plays at its source level;
- a transition and watermark render may take at most 3× the static one;
- memory must not grow with the slides.

**What the render is now** (`core/video_creator.py`, *the deck render*). Python prepares the stills and the narration track and never touches a frame:

- Each slide's image is hard-linked into a scratch folder under a short name. If it has transparency, it is flattened onto black first.
- The title cards and the watermark are drawn once each with Pillow (`_title_card`, `_watermark`), using moviepy's own positions and alpha maths.
- The master track is `_build_master_audio`. It now reports the gap it left after each slide (`gaps_out`), so the picture follows the track; it is otherwise unchanged.

ffmpeg builds and encodes every frame:

- A still is decoded and scaled once (`scale` with lanczos, stretched as `ImageClip.resized` stretched it). It is converted to 4:2:0 once and repeated in memory by `loop`.
- A transition's frames are split off the same still and drawn in RGB, on a clock that follows the slide's exact start on the master track:
  - `fade`, through black or white;
  - `pad` + `crop`, for a slide-in;
  - a per-frame `scale` + `crop` + `fade`, for the zoom.
- That clock is the slide's own time plus one second (`transition_clock_shift`), so it never starts below zero; the fade's start and the slide and zoom expressions are moved by the same second (see the fix round).
- A pause is a `color` source.
- The watermark is one `overlay` over each chunk's concatenated picture. Its still is padded so that it lands on exactly the pixel it was placed at (`even_origin`; see the fix round).
- Boundaries between segments are the cumulative time rounded half up to a frame (`frame_boundaries`); the last one rounds up, so the picture never ends before the track.

Other changes:

- `processing._build_video` chooses the frame rate with `deck_fps`: 24 fps for a transition or an animated slide.
- `encode_settings()` returns the encode as ffmpeg arguments (`video`, `audio`, `container`).
- The chapters read each narration's length from ffmpeg's header (`_probe_duration`).
- moviepy is gone from the app: nothing under `api/`, `core/`, `services/` or `utils/` imports it. `_open_audio_with_retry`, the clip builders and the proglog logger went with it. It stays in `requirements.txt` because `desktop/scripts/check-environment.ps1` and `fetch-python.ps1` import it in their environment checks.

**The architecture, by measurement.** Two shapes were built from the same code and measured on the owner's deck (first round, before the fix round; the fix round changes neither shape). The figures below are ffmpeg's peak working set, summed over its processes and sampled every 50 ms.

- **A:** one filter graph over the whole deck.
- **B:** chunks of at most K segments, each one ffmpeg run, joined by a stream copy.

| Crossfade + the watermark | A | B |
| --- | --- | --- |
| 1080p, 20 slides | 44.4 s, 2,986 MiB | 46.7–52.0 s, 1,373–1,390 MiB (K = 8); 45.6 s, 1,781 MiB (K = 16) |
| 4K, 20 slides | 125.3 s, 9,964 MiB | 131.9 s, 4,029 MiB (K = 4) |
| 1080p, 60 slides | 133.5 s, 7,177 MiB | 138.6 s, 1,390 MiB (K = 8) |
| 1080p, 60 slides, no transition | 77.9 s, 2,831 MiB | 77.8 s, 993 MiB (K = 8) |

On the final code B's peaks are the same: 1,349–1,382 MiB for 20 slides with a crossfade and the watermark (both binaries), 1,396 MiB for 60, 993 MiB for 60 with no transition, 4,039 MiB at 4K (`results_batch5.jsonl`, `results_batch7_8_clean.jsonl`).

**Why A's memory grows.** ffmpeg configures and primes every chain of a graph before its first frame: 1 GB was resident before frame 0 with 40 chains. So A's memory grows with the slides:

- about 11 MB a still at 1080p, scaled straight to 4:2:0;
- 27 MB a still scaled in RGB;
- about 70 MB a slide with a transition's three chains;
- about 220 MB a slide at 4K.

None of these changed that:

- `-threads 1` per input;
- `movie=` sources instead of `-i`;
- one ffconcat input split to every branch (10.6 MB a segment).

**B ships**, with K = 8 at 1080p, 18 at 720p and 4 at 4K, never fewer than 4 (`CHUNK_PIXELS`). A chunk takes more segments than K only while it is shorter than 48 frames (at 2 fps, a run of very short slides). B's memory is the same for 60 slides as for 20, and it costs a few percent of time: one x264 start-up per chunk.

Also measured and rejected:

- **One ffmpeg per segment, in parallel.** x264 `medium` already uses about 11 of the 16 logical cores. 2,400 static 1080p frames took 5.73 s in one run, 5.51 s in two and 5.60 s in four.
- **Raw frames through an OS pipe into one encoder.** It added 30–45 % at 24 fps, whatever the pipe buffer.

**The join** is the concat demuxer with a stream copy and `+faststart`. The frame count is exact, and every frame is the right one, on 7.1 and 8.0.1 for chunks of 2 frames and more. The timestamps are uniform on 7.1 (every step 512 ticks of 1/12288 s at 24 fps); on 8.0.1 the Reviewer found one 508-tick step (0.33 ms short) at the second join of one zoom render. On 8.0.1 a chunk of ONE frame broke the join (fudged DTS, two extra frames decoded). So no chunk is shorter than 48 frames (`MIN_CHUNK_FRAMES`) unless it is the whole deck.

**The sound** (the master track and any music) is encoded by its own ffmpeg run beside the picture's. Inside the first chunk's graph, its AAC encode of 443 s ran on the frames' thread and put about 10 s on a static render's critical path.

**The owner's deck, old and new, on the final code.** Both paths ran through `VideoProcessor._build_video`, not the app, on the i9-9900K (the owner's desktop). The old path is the 7da8e03 snapshot. Other sessions' work (an Ollama model server, an antivirus scan) kept 1.6 to 4.0 cores busy throughout the runs below, measured per run by `measure.py` (`foreign_cores`); runs that had four or more foreign cores were repeated. The old renderer's figures are the first round's, on a quiet machine.

| Render (1080p, `youtube_1080p`) | Old (7da8e03, moviepy) | New, ffmpeg 8.0.1 (the dev box's) | New, bundled 7.1 (the app's) |
| --- | --- | --- | --- |
| No transition | 115.8 s (the owner's 0.11.0 run: 105.0 s) | 26.4, 26.8, 28.0 s | 25.0, 25.2, 25.0 s |
| Crossfade + watermark | 2,522.2 s (the owner's) | 47.8, 48.4, 48.6 s | 49.3, 49.6, 51.5 s |
| Zoom In + watermark | — | 62.4, 65.5 s | 65.0, 66.3 s |
| Crossfade + watermark, first 4 slides | 472.9 s | 9.3 s | — |
| Preview 15 s (text watermark) | — | 2.0 s | 1.9 s |

The other kinds, on 8.0.1, from an earlier batch on the final code that ran beside another session's build and test
run (upper bounds): no transition + text / image watermark 30.5 / 29.4 s; Crossfade alone 54.0 s; Slide Left
54.7 s, with the watermark 52.5 s; Fade to White + watermark 58.4 s; Zoom In alone 76.0 s
(`results_batch5.jsonl`).

**What the renders are made of:**

- **Ratios.** Crossfade + watermark ÷ no transition: 1.7–1.8× on 8.0.1 and 2.0–2.1× on 7.1; Zoom In + watermark: 2.2–2.5× on 8.0.1 and 2.6–2.7× on 7.1. Every pairing is under the owner's 3×. The new static render is 4.4× (8.0.1) to 4.6× (7.1) faster than the old one.
- **Time split, static render.** Building the master track takes about 13 s (pydub: trims, levels, MP3 export). Writing the video takes 12.6–13.3 s, most of it the sound's AAC encode: 48–50 % of the render.
- **Time split, crossfade + watermark.** The encode is 33.9–38.8 s of 47.8–51.5 s (70–75 %); with Zoom In, 48.2–53.6 s of 62.4–66.3 s.
- **CPU.** A static render costs about 55 CPU-seconds; a crossfade + watermark one about 350 (7 cores over 48 s).
- **Peak memory, new.** 0.96–1.0 GB at 2 fps, 1.35–1.38 GB with the crossfade, 1.13–1.15 GB with the zoom (ffmpeg); Python about 104 MiB.
- **Peak memory, old.** Python 1,930 MiB plus ffmpeg 914 MiB.
- **Files.**
  - Every new file: H.264 High, level 4.0 (5.1 at 4K), yuv420p, untagged like the old one, AAC 48 kHz stereo at 181 kbit/s (193 with music).
  - Sizes: 12.70 MB static, 16.21 MB crossfade + watermark, 17.54 MB zoom + watermark; the preview is 0.61 MB and 16.0 s.
  - Frame counts: 887 frames at 2 fps (443.5 s), 10,633 at 24 (443.04 s), for a 443.01 s track.

**Parity, frame against frame, on slides of both lead signs.** `parity2.py` renders a short deck through the old renderer and the new one:

- the owner's first three slide images (slide 1 is dark, slides 2 and 3 bright);
- the first 3 s of each narration;
- an intro card with a subtitle and an outro card;
- the watermark;
- 1080p;
- a 0.53 s pause after slide 1 and 0.51 s after slide 2, so that slide 2's first frame is 0.36 of a frame BEFORE its exact start (its start rounds down: a negative first timestamp) and slide 3's is 0.36 of a frame after it (its start rounds up).

Frame n of each render is compared at the same timestamp with ffmpeg's `ssim` and `psnr`.

- **Kinds whose look did not change** (none, Fade to Black, Crossfade, Slide Right, Slide Down, Zoom In):

  | Frames | SSIM | PSNR |
  | --- | --- | --- |
  | Mid-slide | 0.9954–0.9995 | 43.2–52.6 dB |
  | Intro card | 0.9998–1.0000 | 68.3 dB to identical |
  | Pause | 1.0000 | identical |
  | Watermark corner on slide 1 (dark: the white mark is visible; bottom-right 220×60) | 0.9999 | 52.6–52.7 dB |

  Through the transitions, at 25, 50 and 75 % (SSIM, PSNR):

  | Transition | Slide 2 (start rounds down) | Slide 3 (start rounds up) |
  | --- | --- | --- |
  | fade-in (Fade to Black, Crossfade), first frame | identical (black) | 0.9780, 45.2 dB (3 % of the way in) |
  | fade-in, 25–75 % | 0.9940–0.9969, 42.1–48.7 dB | 0.9937–0.9956, 40.7–46.8 dB |
  | Slide Right, first frame and 25–75 % | identical; 0.9985–0.9999, 45.4–56.5 dB | identical; 0.9973–0.9996, 44.2–52.4 dB |
  | Slide Down, first frame and 25–75 % | identical; 0.9976–0.9994, 42.5–49.0 dB | identical; 0.9974–0.9994, 44.3–51.3 dB |
  | Zoom In, first frame and 25–75 % | identical; 0.9916–0.9942, 40.3–45.6 dB | 0.9856, 48.2 dB; 0.9884–0.9935, 35.5–45.0 dB |

  The fade-out: on slide 2 (bright) 0.9954–0.9969, 43.6–46.8 dB; on slide 1 (dark) 0.9992 / 54.7 dB at 50 %, but 0.9717 / 38.2 dB at 25 % and 0.9161 / 48.0 dB at 75 % (see below).
- **The low fade-out figures are on the dark slide, and the old renderer is the one that is off.** 85 % of slide 1 is one dark colour, (18, 21, 25). A fade multiplies those small values, and the two renderers quantise the result differently: moviepy truncates the faded value when it composites, ffmpeg's `fade` rounds. With no encoder on either side (`fadeout_quant.py`: moviepy's frames as it hands them to its encoder, against ffmpeg's `fade` on the same still):

  | Frame (ideal factor) | Old | New | That colour: ideal | Old | New |
  | --- | --- | --- | --- | --- | --- |
  | 114 (0.720) | 0.703 | 0.720 | 12.96, 15.12, 18.00 | 12, 15, 17 | 13, 15, 18 |
  | 117 (0.470) | 0.452 | 0.470 | 8.46, 9.87, 11.75 | 8, 9, 11 | 8, 10, 12 |
  | 120 (0.220) | 0.203 | 0.227 | 3.96, 4.62, 5.50 | 3, 4, 5 | 4, 5, 6 |

  The old fade-out is darker than the ideal fade by 0.6–0.7 of a level a pixel; the new one is within 0.02–0.29 of it. The Reviewer measured the same with lossless x264 in both renders (the dips stay: SSIM 0.971 and 0.917; at frame 114 the ideal factor is 0.720, the old 0.632, the new 0.717), so the encoder is not the cause, as the first version of this note said it was. On the bright slide 2 the two agree with the ideal to 0.003 on every frame of the fade-out but its last, read from the encoded files (the last frame, at 3 % brightness, belongs to the pause in the new render's rounding).
- **The three kinds the owner chose to fix differ during their transitions only, as intended.** Their slides, cards and pauses are 0.9967–1.0000.
  - Fade to White: fade-in SSIM 0.89–0.99; fade-out 0.37–0.95 (the old one cut to black first).
  - Slide Left and Slide Up: SSIM 0.05–0.56, now mirrored.
- **One 2 fps point compares black with a slide.** The pause after slide 1 runs from 5.11 s to 5.64 s. The new render's round-half-up boundaries put it on frame 10 alone (5.11 × 2 = 10.22 and 5.64 × 2 = 11.28). moviepy sampled each frame at k/2 s, which put the pause on frame 11 (5.5 s). Both are within a frame of the track.
- **The same check on the owner's whole deck** (`fadein_check.py`: each of the 19 slides that fade in, its first frame against its settled one): Crossfade + watermark, Crossfade alone, Crossfade at 4K, Zoom In with and without the watermark and Fade to White all start at their fade's colour, 19 of 19. Eight of the 19 start before their frame.

**The narration and the music** (`audio_check.py`, on the full-deck renders, against the master track the render was given):

- **Narration level.**

  | Voiced RMS | dB | Against the master |
  | --- | --- | --- |
  | Master track | -20.07 | — |
  | New render, each channel | -20.07 | -0.004 dB |
  | Old render | -23.09 | -3.016 dB (moviepy's reader asked ffmpeg for `-ac 2`: a mono-to-stereo up-mix at -3 dB) |

- **Clipping.** No sample is at or over full scale in any of these: the master, the new render, the new render with the default music volume (0.25) under a two-track playlist and under one looped track, and the two library tracks. The peaks are 0.644 for the master and the render, 0.660 with music, 0.12 and 0.19 for the tracks. At the default volume, even a full-scale track adds at most 0.25 to the narration's 0.644.
- **Music behaviour.** The playlist order, the whole-playlist loop, the single-track loop, the fade in and out and the linear volume are read back from real renders by `tests/test_deck_render.py`.
- **The end of the track.** The picture's last boundary rounds up, so the sound is never cut before the narration ends: the 443.0096 s track now gets 443.5 s of picture at 2 fps and 443.04 s at 24, where rounding to the nearest frame gave 443.0 s and dropped the track's last 9.6 ms (up to half a frame on another deck: 250 ms at 2 fps). The chapters are identical before and after, and the per-slide SRT is identical to the 7da8e03 one (`end_check.py`).

**The scaler.** Each of the 20 slides (960×540) was stretched by Pillow's LANCZOS (moviepy's) and by ffmpeg's `scale=...:flags=lanczos`:

| Size | SSIM min / mean | PSNR min / mean |
| --- | --- | --- |
| 1080p | 0.99989 / 0.99994 | 55.8 / 59.5 dB |
| 4K | 0.99981 / 0.99989 | 56.3 / 59.8 dB |

ffmpeg's `bicubic` would be 0.9979 / 44.3 dB.

**Animated slides** (`anim_drift.py`: a red slide, a 4 s clip whose narration is 2 s, a blue slide; 0.5 s pauses):

| | Next slide starts | Master track says | Drift | Clip shown | Distinct pictures | Frame rate |
| --- | --- | --- | --- | --- | --- | --- |
| Old (2 fps, as processing chose) | 7.0 s | 5.0 s | +2.0 s | its own 4 s | 8 | 2 fps |
| New | 5.0 s | 5.0 s | 0.0 s | its 2.0 s span | 48 | 24 fps |

**The fix round** (the review's B1, M1, M2, m1, m3, n1, n2; m2, wiring the cancel route to a deck render, is the owner's call and is not in it).

- **B1: the fade-in was skipped on every slide whose start rounds down to a frame.**
  - Such a slide's first frame is up to half a frame before its exact start, so the transition's first timestamp was negative, and ffmpeg's `fade` never fades from a negative first timestamp: every frame of the slide came out at full brightness. In isolation, on 7.1 and 8.0.1 alike (`fade_probe.py`, a grey 200 still, 0.5 s fade at 24 fps): first timestamp −0.24 frame gives luma 200 on every frame; 0 gives 0, 17, 33, 50…; +0.28 gives 5, 21, 38….
  - On the owner's deck 8 of the 19 fading slides cut in unfaded (Crossfade and Zoom In alike), exactly the 8 whose start rounds down.
  - The first round missed it because every slide its tests and its parity deck checked had a start that rounds up.
  - The fix: every transition chain's clock is the slide's own time plus one second, and the fade's start and the slide and zoom expressions move with it. With the shift, each lead from −0.5 to +0.499 of a frame gives the ideal fade on both binaries (−0.24: 0, 13, 29, 46, 63…).
  - After it: 0 of 19 unfaded, in every render checked above.
- **M1: the watermark was drawn one pixel up or left at an odd coordinate.**
  - In 4:2:0 `overlay` snaps an odd x or y down to even. The owner's mark is placed at (1826, 1057); it was drawn on rows 1059–1075 where the old renderer drew it on 1060–1076.
  - On slide 1's dark corner, old against new, before the fix: PSNR 25.5 dB as drawn, 46.7 dB with the new corner moved down one row. The first round's "corner SSIM 1.0000" was measured on slide 2, whose corner is white, where the white mark cannot be seen.
  - Two cures were measured on the owner's crossfade + watermark render (`corner.py`, `measure.py --overlay`). All three land the mark on rows 1060–1076:

    | Cure | Render | Corner against the old renderer |
    | --- | --- | --- |
    | the still padded to an even origin | 50.0 s, 52.1 s | 52.65 dB, SSIM 0.9996 |
    | `overlay=format=yuv444` | 61.0 s, 65.6 s | 52.65 dB, SSIM 0.9996 |
    | `overlay=format=rgb` | 61.7 s, 70.9 s | 46.63 dB, SSIM 0.9987 |

  - The padded still ships: it costs nothing, where converting every frame to 4:4:4 or RGB and back costs 10 to 19 s.
- **M2: the cancel test flaked.** ffmpeg's first `-progress` block can say `frame=0` before x264 has given it a frame, and the test asserted the first report was above zero. It now asserts the reports never go back and end above zero. Run ten times per binary after the change: 10 of 10 green on the bundled 7.1 and 10 of 10 on 8.0.1 (`cancel_x10.log`).
- **m1: three paths no test guarded** now have one each, on both binaries: progress counted across the chunks of a render; a join that fails after the partial file is written, or is cancelled while it writes, leaves no `.part.mp4`; a sound encode that fails is reported as a sound failure. The code already did all three.
- **m3: this note's claims.** The plant table below is from the final file. The fade-out dips' cause, the `-ac 2` wording and the join's exactness are corrected above.
- **n1: the picture never ends before the track** (the last boundary rounds up; see *The end of the track*).
- **n2: wording.** The module's docstring no longer says "one run"; the guide and the limits page give the part size per resolution.

**Found on the way.**

- **The transition sound.**
  - After a narrated slide, the master track's gap is the longer of the pause and the sound, which the old pause clip matched.
  - After a SILENT slide, the track's gap is the pause alone, while the old pause clip held for the sound's length. The picture now follows the track.
  - The chapters and the per-slide SRT still use the pause alone. Wherever a sound outlasts the pause, they run early by the difference, unchanged as the brief asked.
  - No route passes a transition sound or background music to a deck render today.
- **The chapters' lengths depend on the binary.**
  - They come from the header's `Duration`, which moviepy's `AudioFileClip` read too. On the bundled 7.1 that number equals moviepy's for 20 of 20 clips, so the product's chapters are unchanged.
  - ffmpeg 8.0.1 estimates an MP3's length more closely: 433.52 s over the 20 trimmed clips, against 433.51 s decoded and 434.70 s on 7.1.
  - So on 7.1 the chapters have always drifted about 60 ms a slide late against the picture; the last one ends 1.2 s past the end of the video.
- **The zoom.** `crop`'s `iw` keeps the first frame's width when `scale` changes size frame by frame, so the zoom tells `crop` the size by the same expression.
- **Fades through a colour.** `fade` with a colour other than black takes RGB only. So a transition's frames are drawn in RGB and converted once each.
- **Colour tags.** A slide PNG says sRGB/BT.709, while moviepy's raw RGB said nothing and the file went out untagged. `setparams` drops the tags, so the conversion and the file are as before.
- **A default argument.** `deck_chunks`' default argument was bound at definition time, so a patched `MIN_CHUNK_FRAMES` never reached the render. It is read at call time now.
- **Other work on the machine.** Timings on this desktop are only comparable when nothing else is running: a first re-timing ran beside another session's build and test run and read up to twice as long. `measure.py` now waits for an idle machine and records what every other process used during each render.

**Follow-ups for the owner.**

- Crossfade is a fade through the black pause, not a dissolve; it is kept as it was.
- `POST /api/jobs/{id}/cancel` does not reach a deck render (the review's m2): the render polls a flag no route sets.
- The chapters could use the master track's own spans: exact on every binary, and following a transition sound.
- moviepy can leave `requirements.txt` once the two desktop environment checks stop importing it. imageio-ffmpeg, the ffmpeg the installer ships, is pinned on its own.
- Most of a static render is now the pydub master track.
- The zoom's per-frame lanczos is its extra cost.

**The proof.** `tests/test_deck_render.py` covers these pure checks:

- cumulative rounding over 560 odd durations at 2, 24, 25 and 30 fps, and the last boundary rounding up;
- the timeline following the track;
- the gaps the track reports;
- each kind's plan;
- `deck_fps`;
- the chunk limits, and the claim that 60 slides need no bigger chunk than 20;
- the timeouts;
- the sound graph and the music graph;
- the chunk graph's exact-start timing;
- no transition chain's clock below zero, for every kind and for animated slides, over decks with more than 50 slides whose start rounds down;
- `even_origin`.

On every real ffmpeg it also checks:

- each of the nine transition kinds rendered from a three-slide deck (two stills and a looped animated clip, a title card and the watermark), read back for:
  - the encode;
  - the frame count against the decoded master track;
  - mid-slide quadrants;
  - mid-fade between the slide and black or white;
  - each direction's half-way frame;
  - the zoom darker and enlarged;
  - the watermark's blend;
  - the card;
  - the animated clip looped and moving;
  - the narration within 0.5 dB of the master;
- each fading kind (Fade to Black, Crossfade, Fade to White, Zoom In) on a still and an animated slide whose start rounds down, and on ones whose start rounds up: the first frame at the fade's colour, half way at half way;
- the watermark pixel by pixel against a reference composite on a dark slide, at an odd position (0.8 of a grey level a pixel where it was placed, 10 or more one pixel off);
- a 2 fps render with no outro card whose narration ends between frames: 5 frames for 2.2 s, and the last 0.2 s of the narration in the file;
- a deck in several chunks against the same deck in one;
- progress across the chunks of a render;
- a join that fails or is cancelled, and a sound encode that fails;
- the music's order, loop, fades and levels, as a playlist and as a single track;
- a cancel mid-encode: stopped within 2 s, nothing left, progress never going back.

`tests/test_encode_settings.py` reads the encode from the chunk's command, the sound's command and the join's. `tests/test_fonts.py` draws the cards and the watermark as stills.

**Each check was watched failing** under a plant that breaks what it guards, on the final `core/video_creator.py` (sha256 `973db62d254c009987d524732d491eadd37e6d60a86d326a2e5e7e49a22bb932`). Each plant was restored from a copy and the file's sha256 verified against that hash. F = the fix round's, P = the first round's, R = the Reviewer's own:

| Plant | What it broke | Red because | Result |
| --- | --- | --- | --- |
| F01-clock-shift-removed | the transition clock is not shifted (a negative first timestamp again) | fade-to-black, still, lead -0.24: the first frame is 1.00 of the way in | 6 failed |
| F02-watermark-snapped | the watermark is overlaid at its odd corner unpadded (snapped up/left by overlay) | the frame is 18.28 grey levels a pixel from the reference at (141, 85) | 3 failed |
| F03-last-boundary-nearest | the picture's end rounds to the nearest frame (may end before the track) | 2.2 s of track is 5 frames at 2 fps, never 4 | 2 failed |
| F04-progress-not-offset (R10) | progress reports each chunk's own frame count, not frames of the render | the count went back: [78, 71, 75, 25, 249] | 1 failed |
| F05-part-left (R11) | the .part.mp4 is not removed after a failure or a cancel | assert ['deck.part.mp4'] == [] | 2 failed |
| F06-sound-failure-ignored (R12) | a failed sound encode is not reported | ['Error creating video: ffmpeg failed (exit 4294967294): [in#1 @ 00000000027b5e40] Error opening input: No ... | 1 failed |
| F07-fade-in-at-zero-not-shift | the fade-in starts at 0 of the shifted clock (a second early: over before the slide begins) | fade-to-black, still, lead -0.24: the first frame is 1.00 of the way in | 2 failed |
| P01-cumulative-rounding | boundaries rounded segment by segment | a boundary is 15.076 frames from its time at 2 fps | 1 failed |
| P02-timeline-follows-track | the pause setting instead of the track's gap | the 3.2 s gap is the track's (a transition sound longer than the 3 s pause), not the pause setting | 1 failed |
| P03-master-reports-gap | the track reports the pause, not the sound's gap | sound 2.5 s > pause 1 s; silent: the pause; 3 s pause > sound; last: none | 1 failed |
| P04-slide-left-direction | slide-left from the left again | ('slide-left', (0.2, 0.25), array([ 38., 198.,  38.]), (0, 0, 0)) | 1 failed |
| P05-slide-up-direction | slide-up from the top again | ('slide-up', (0.25, 0.15), array([ 39.,  39., 219.]), (0, 0, 0)) | 2 failed |
| P06-fade-through-white | the white fade drawn through black | ('fade-to-white', 'tl', 0.5166666666666657, array([113.,  19.,  19.]), array([236.91666667, 143.91666667, 1... | 1 failed |
| P07-zoom-enlarges | ZOOM_FROM = 1.0 | (80, 80) | 1 failed |
| P08-fade-black | crossfade's fade-in removed | ('crossfade', 'tl', 0.5166666666666657, array([220.,  39.,  39.]), array([113.66666667,  20.66666667,  20.6... | 1 failed |
| P09-unity-upmix | aformat=channel_layouts=stereo instead of the unity pan | the narration is -3.03 dB against the master track | 2 failed |
| P10-48k | no aresample=48000 | assert ('aac', '24000', 2) == ('aac', '48000', 2) | 1 failed |
| P11-cancel-stops-ffmpeg | the cancel ignored while ffmpeg runs | the encode was stopped, not left to finish | 1 failed |
| P12-progress-reported | no progress reported | the cancel came mid-encode | 1 failed |
| P13-chunks-back-to-back | chunks overlapping by a segment | frame 78 differs across the join | 2 failed |
| P14-chunk-minimum | no minimum chunk length | assert False | 1 failed |
| P15-animated-loops | no -stream_loop for an animated slide | assert 214 == 249 | 1 failed |
| P16-playlist-loops | the playlist not looped | the playlist again from its first track: looped | 1 failed |
| P17-single-track-loops | a single track not looped | the next track in order | 1 failed |
| P18-music-fades | no music fades | faded in | 2 failed |
| P19-watermark-corner | the watermark placed 30 px left | assert (534, 337) == (564, 337) | 2 failed |
| P20-watermark-blend | the watermark at opacity 1.0 | the corner's blend | 2 failed |
| P20b-watermark-overlaid | the overlay disabled | the corner's blend | 2 failed |
| P21-card-title-height | the title at 10 %, not 40 % | the title at 40 % | 2 failed |
| P22-picture-follows-intro | the intro card 0.5 s longer than the track's silence | (10.875, 9.349416666666666) | 1 failed |
| P23-faststart-on-the-join | no +faststart on the join | ['ftyp', 'free', 'mdat', 'moov'] | 1 failed |
| P24-animated-fps | an animated deck at 2 fps | assert 2 == 24 | 2 failed |
| P25-pause-is-the-slides-own | the job's pause for every slide | after the last slide, none | 1 failed 2.03s |
| P26-profile-to-the-master-track | no onset profile to the track | assert [None, None] == [OnsetProfile...db=8.0), None] | 1 failed 1.50s |
| P27-sound-bitrate | no -b:a for the sound | ('128287', '192k') | 3 failed |
| P28-frame-rate-argument | no -r in the encode | assert {'video': ['-...'+faststart']} == {'video': ['-...'+faststart']} | 2 failed |
| R02-frame-dropped-at-join | the first segment of every chunk after the first loses its first frame | (6, [77, 78, 148, 149, 223, 224, ...]) | 2 failed |
| R03-frame-duplicated-at-join | the last segment of every chunk but the last gains a frame | frame 148 differs across the join | 2 failed |
| R06-cancel-spares-sound | a cancel kills the picture's ffmpeg but never the sound encode | the sound encode was left running | 1 failed |
| R09-chunk-limit-ignored | the render ignores the chunk limit (one graph for the whole deck) | 1 chunk(s) | 2 failed |
| R13-white-pause-black | the fade-to-white pause is black | the pause | 2 failed |
| R14-scratch-left | the stills scratch folder is not removed | the scratch is gone | 2 failed |
