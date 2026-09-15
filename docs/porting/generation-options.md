# Porting spec — generation options (vertical 2)

> **Status: 2a is DONE**, shipped in 0.3.0 (transitions, intro and outro cards, the text
> watermark, subtitle modes, the extra formats, the preview render, and the four engine
> defects that had to be fixed for any of it to work — see the `#generation-options` entry
> in CHANGELOG.md). **2b is NOT started**: the music and transition-sound library, section
> 4 below, is the only part of this spec still outstanding. Everything above it describes
> what shipped, so read it as a map of the code rather than as work to do.

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
