# Porting spec — slide editor + AI assistant (vertical 3)

> **Status: DONE.** Both halves shipped in 0.3.0 — 3a the slide editor (thumbnail rail,
> notes editing with undo and reset, per-slide voice and pause overrides, export with the
> edited notes written back, slide previews behind a shared PowerPoint lock) and 3b the AI
> assistant (notes, enhance, QA review with per-issue fixes, tone, translate, pacing,
> analyze, the Q&A document, and per-slide enhance as a revertible proposal). See the
> `#slide-editor` and `#ai-assistant` entries in CHANGELOG.md. This file is now a map of
> the code, not work to do.

Read-only survey, 2026-09-14. `core/` here is a byte-identical copy of SlideStudio's
(only relative→absolute imports differ), so every engine entry point below exists at the
same lines. The work is API + UI + one service layer, not engine porting.

## 1. Per-slide state already exists — it is unexposed

- `core/project_manager.py:11-30` `SlideRenderState`: index, speaker_notes, audio_path,
  image_path, video_path, audio_duration, voice_id, needs_regeneration, ai_enhanced,
  pause_override, has_animation, voice_override, alt_text, notes_history,
  original_start_time, original_end_time, original_segments. `speaker_notes` IS the edited
  value (no separate field); `ProjectState` `:33-56`.
- Persistence: `save()` `:156-185` hand-serialises (a new field is silently lost unless
  added there — switch to `asdict`). Mutators auto-save: `update_slide_notes` `:230-249`
  (history capped at 20, sets needs_regeneration), `undo_slide_notes` `:251-261`,
  `update_slide_pause` `:342-347`, `get_slides_needing_regeneration` `:285-319`.
- Two `project.json` layers on disk: the web record `data/projects/<pid>/project.json`
  (`services/projects.py:106-116`: id, name, kind, source_filename, size_bytes,
  slide_count, created_at + transcript/output_video/revoiced_video) and the engine state
  `data/projects/<pid>/<stem>_project/project.json` created by `FileItem(projects_base=…)`
  at `api/routers/projects.py:109` — but only when generate runs (PDFs at import,
  `services/file_item.py:41-65`). The 40-slide sample deck already has all 17 keys +
  `images/slide_001.png…`.
- Gaps: nothing materialises the inner project before generate; no API reads/writes it;
  `slide_count` on the outer record is never reconciled; `pause_override` is consumed by
  nothing in the render path; `voice_override` IS honoured (`services/processing.py:576-580`)
  but has no writer in any UI.
- Notes come from `core/pptx_reader.py:37-75` (notes + title + body_text incl. table rows);
  PDFs have NO notes (`core/pdf_reader.py:118-124` returns "").

## 2. Slide images

- `core/pptx_exporter.py:429-444` `export_slides_as_images`: PowerPoint COM
  (`_export_via_powerpoint` `:213-258`, attaches to a running PowerPoint via
  `GetActiveObject`, animations → MP4 with a 120 s busy-wait) else `_export_via_pillow`
  `:446-497` (white 1920x1080 with the title only). `_export_via_libreoffice` `:264-358`
  is DEAD CODE (never called) though it would be a real, thread-safe second choice.
- Media Studio exports only inside generate (`services/processing.py:240-260`, guarded by
  "all images exist" so it is idempotent/cached). SlideStudio pre-rendered via
  `gui/helpers.py:36-51` `ensure_preview_images`.
- Images live at `<pid>/<stem>_project/images/slide_NNN.png`, recorded as ABSOLUTE
  Windows paths — never serialise; serve by index through a route with the
  `base not in path.parents` guard from `api/routers/projects.py:168-171`. No static mount
  exists except `frontend/dist/assets` (`api/app.py:68-71`).

## 3. AI features — engine entry points

- `core/ai_assistant.py` is the chat/command router (QUICK_ACTIONS `:19-77`,
  `interpret_command` `:266-568`), takes a NiceGUI AppState — not the notes engine.
- The per-slide vision work lives in SlideStudio's GUI (`gui/components/generation_ai_actions.py`:
  auto-generate notes for empty slides `:130-243`, enhance all `:246-368`, QA review
  `:371-563`; `gui/components/preview_panel.py`: per-slide enhance `:908-991`, per-criterion
  QA fix `:477-560`). All build one prompt shape (`:204-212` / `:312-325`: image preamble,
  Slide title, Slide content, Existing speaker notes, instruction) and call
  `core/ollama_client.py:272-307` `generate(prompt, model, system, base_url, timeout=120,
  images=[path])`; non-vision models silently ignore images. Also `check_connection` `:30`,
  `list_models` `:40`, `MODEL_CATALOG` vision list `:198-204` (gemma3:*, llava:*,
  moondream); configured default `gemma3:12b` (`utils/config.py:109`, vision-capable).
- `core/qa_generator.py:14-83` `generate_qa(notes, url, model, num_questions=10)` →
  `[{question, answer, slide_ref}]` (4000-char cap, returns [] on failure);
  `export_qa_document` `:135-157`.
- `core/slide_analyzer.py:17-111` `analyze_slide_content` — deterministic scoring +
  optional LLM prose (20-slide cap, 30 s): fast, sync-safe. `extract_slide_data` `:158-192`.
- `core/tone_adapter.py:49-109` `adapt_notes(notes, tone, url, model, custom_prompt)` —
  TONE_PRESETS Technical/Executive/Student/Sales/Casual/Formal + Custom; one call per
  slide, 30 s each, falls back to the original.
- `core/translator.py:96-123` `translate_notes` (14 languages, `GET /api/languages`
  exists); SlideStudio's Translate modal (`generation_ai_actions.py:1274-1325`) replaces
  notes in place and optionally switches the voice via `_switch_to_language_voice`.
- `core/auto_pacing.py` rules-only `apply_pacing_to_notes` (instant) / `ai_pacing`.
- QA review output: strict JSON `{"score":1-10,"slides":[{"slide":n,"grammar","tone",
  "flow","transitions"}]}`, parsed by `_parse_qa_json` (`:105-127`), mapped POSITIONALLY —
  validate the count. "No issue" sentinel set at `:513`.
- Everything is blocking I/O → jobs (`services/jobs.py` turns exceptions into
  status=error). Batch loops must swallow per-slide errors and count them.

## 4. Export updated PPTX

`core/pptx_reader.py:102-138` `export_with_updated_notes(source_pptx, updated_notes,
output_path)` — never called by any app code. python-pptx only (no COM, thread-safe,
fast → a synchronous FileResponse route). Notes formatting is replaced by one plain run;
the list must be index-aligned (it breaks early on a short list).

## 5. SlideStudio's UI to mirror

- Whole-deck buttons (`gui/components/generation_panel.py:173-193`, `:629-675`): Generate
  (notes for empty slides), Enhance, QA; Subtitles, Q&A doc, Analyze; One-Click, Tone,
  Split, Pacing, Translate.
- Per-slide editor (`preview_panel.py`): notes textarea with an unsaved dot; AI Enhance →
  loads a PROPOSAL into the editor without saving and reveals Revert; Reset (original
  PPTX notes); Undo (20 steps); Save (Ctrl+S); Copy; inline QA issues with per-criterion
  Fix → "Fixed"; prev/next with arrow keys; move slide up/down; "Animated" badge.
- Re-upload guard (`gui/helpers.py:124-143`): Cancel / Keep my edits & refresh /
  Overwrite; slide-count-changed warning `:170-176`. Not needed until "replace source deck"
  exists (imports mint a new pid today).
- Feedback: status "AI Enhance: i/total slides…", buttons loading, final toast with the
  error count.

## 6. Recommended API (prefix /api/projects/{pid}, deck/pdf only)

- `GET /slides` → `{slides:[{index, speaker_notes, title, body_text, has_image, has_audio,
  ai_enhanced, needs_regeneration, voice_override, pause_override, alt_text,
  notes_history_depth}]}` — materialises the inner project from PPTXReader if absent
  (no image export).
- `PATCH /slides/{i}` {speaker_notes?, voice_override?, pause_override?, alt_text?};
  `POST /slides/{i}/undo`; `PATCH /slides` bulk {slides:[{index, speaker_notes}]}.
- `POST /slides/render` (job "render-slides"; 200 {cached:true} when all_images_ready);
  `GET /slides/{i}/image` (FileResponse by index, cache-bust query).
- Jobs: `POST /ai/notes` {scope: empty|all, slide_indexes?, use_vision}, `POST /ai/enhance`,
  `POST /ai/qa`, `POST /ai/tone` {tone, custom_prompt?}, `POST /ai/translate` {language,
  match_voice}, `POST /ai/qa-doc` {num_questions}, `POST /ai/pacing` {use_ai}.
  Sync: `POST /slides/{i}/ai/enhance` → {suggestion} (does NOT save),
  `POST /slides/{i}/ai/qa-fix` {criterion, issue}, `POST /ai/analyze`, `GET /ai/status`
  → {available, models, configured_model, vision_capable}, `GET /export/pptx`.
- Persist QA results on the outer record keyed by slide index: `qa_review {score, run_at,
  model, slides:[…]}`; outer record gains `slides_ready`, `notes_source`, `ai_model`,
  `engine_project_dir`.
- `services/slides.py`: `manager(pid)`, `list_slides`, `update_slide`, `ensure_images` —
  adopt the engine's store, add a per-pid lock (project.json has none).

## 7. UI layout

Card "Slides" above the Generate card for decks/PDFs: toolbar (Generate notes · Enhance
all · QA review · Tone · Translate · Pacing | Analyze · Q&A doc · Export .pptx), an AI
status banner (Ollama unreachable / model + vision capability), JobProgress for
`ai-*`/`render-*` kinds, then a two-pane body: thumbnail rail (index, notes/audio dots,
QA badge) + selected slide image, read-only title/body, notes Textarea with unsaved dot,
[AI Enhance] [Revert] [Reset] [Undo] [Save], QA rows with Fix → Fixed, per-slide voice
override + pause. Render thumbnails lazily on first open ("Render slide previews", a job).

## 8. Traps

1. PowerPoint COM is single-instance; jobs run two workers → serialise rendering behind a
   lock or a 1-worker executor; CoInitialize per thread; never on the event loop.
2. The Pillow fallback poisons vision (title-only white slides) → disable `use_vision`
   when the fallback rendered, or revive `_export_via_libreoffice`.
3. Non-vision models fail silently → check the model against the Vision catalogue and
   surface it in `/ai/status`.
4. Large decks: sequential per-slide calls with 120 s timeouts (40 slides ≈ 80 min worst
   case) → i/total progress + a cancel flag on Job; QA review sends every note in one
   prompt → chunk it.
5. Absolute Windows image paths must never reach the browser.
6. `project.json` has no lock; add a per-pid lock in the service.
7. Positional QA mapping: reject a wrong-length array rather than mis-map.
