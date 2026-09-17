# Porting spec — narration timeline (vertical 5)

Read-only survey, 2026-09-15. **This is not a port.** SlideStudio's "Timeline Editor"
(`C:\Projects\slidestudio_enterprise\gui\components\timeline_tab.py`, 147 lines) is a
per-*slide* duration-bar list with a pause spinner (`:75-141`) — no waveform, no drag, no
concept of a transcript or a video. Nothing in it is reusable. The engine entry point it
fed, `prepare_audio`, is still here (`services/processing.py:477-485`) with a docstring
that still names that tab and no route (already noted in `generation-options.md` §7).
Everything below is new work over code that already exists in Media Studio.

## Headline

Re-voicing pins every Whisper sentence to the moment it was spoken
(`services/processing.py:1023`) and `assemble_master` (`:190-214`) turns the leftover time
back into silence. That is correct and it is why the picture stays in step
(CHANGELOG `#revoice-sync`). It is also why a narrator who spoke slower than the TTS
produces 71 s of accumulated silence: 240 s of speech returning as 169 s is not drift, it
is 71 s of gaps the app faithfully reproduced. **No automatic fit is designed here.** The
answer is per-sentence overrides the user sets by hand, stored on the transcript, honoured
by `_revoice_video`, and edited against a waveform of the original audio.

Two facts shape the whole design:

- **A pin is a floor, never a position.** `assemble_master:207-213` inserts silence only
  when `gap > 0`; a chunk whose pin is behind the cursor simply follows the previous one.
  So an offset can always push a sentence *later*, and can pull it *earlier* only as far as
  the previous sentence's synthesised audio actually ends. The UI must say this, not
  promise frame-accurate placement.
- **The overrun is self-healing, not cumulative.** A sentence that runs long eats into the
  following gap; the sentence after it is still pinned, so the error is absorbed at the
  next real pause.

---

## 1. Survey

### 1.1 Where the transcript lives

`data/projects/<pid>/project.json`, on the outer web record — the same file that carries
ownership, `outputs`, `revoiced_video` and `narration_audio`. Written as
`record["transcript"] = [{"start", "end", "text"}, …]`
(`services/transcription.py:48-52`), rounded to 3 dp. Alongside it the transcribe step
records `language`, `duration`, `transcribed_model`, `transcribed_device` (`:53-56`).

There is a **second** `project.json` per project — the engine's `ProjectState`
(`core/project_manager.py:47-66`) — and for a video it lives under
`data/projects/<pid>/revoice/` and is **recreated from scratch on every re-voice**
(`services/revoice.py:79-90`). Nothing may be stored there: it does not survive a run.

### 1.2 Every reader and writer, exhaustively

Writers (three, all wholesale):

1. `services/transcription.py:52-57` — sets the whole list from Whisper and saves the
   record **it read at `:23`**. Anything written to `project.json` while a transcribe job
   runs is silently reverted.
2. `services/projects.py:212-219` `set_transcript(pid, transcript)` — `get_project` →
   replace the key → `save_project`. No lock.
3. `api/routers/projects.py:156-165` `PATCH /{pid}/transcript` — the only route. Calls
   `require_project` (`:159`) but **not** `jobs.require_idle`, unlike every slide write
   (`api/routers/slides.py:42-47`). Audits `PROJECT_TRANSCRIPT_EDIT` (`:163-164`).

Readers (four):

1. `api/routers/projects.py:114-116` `GET /{pid}` returns the entire record — this is the
   SPA's only source of the transcript.
2. `api/routers/projects.py:279-280` — the "transcribe first" guard on re-voice.
3. `services/revoice.py:35` (`segments`), `:49` (`joined`), `:83-88` (copied into
   `slide0.original_segments` / `original_start_time` / `original_end_time`).
4. `frontend/src/pages/ProjectDetail.tsx:114-116` copies it into `segments` state,
   `:530-539` renders one `Textarea` per segment, `:541-543` posts the whole array back.

Neither `services/slides.py` nor `services/ai_slides.py` touches it — both are gated to
`SLIDE_KINDS = ("deck", "pdf")` (`services/slides.py:55`, `:114-122`).

**What has to change:** `api/schemas.py:96-99` `TranscriptSegment` has **no**
`model_config`, so pydantic's default `extra="ignore"` applies — the moment override keys
exist on disk, today's Save button round-trips them through this model and **silently
deletes every one**. That is the single most dangerous fact in this survey.

### 1.3 Where re-voice reads it

`services/revoice.py:29-118` builds a one-section `ProjectState`: the joined transcript
becomes `speaker_notes` (`:49-50`, `:80`), the segments become `original_segments`
(`:83-86`) with only `start`/`end`/`text` copied, and `_revoice_video` is called at `:100`.
`ProjectManager.save()` uses `asdict` (`core/project_manager.py:202-210`), and
`original_segments` is a `List[dict]`, so **extra keys inside those dicts survive the JSON
round trip for free** — nothing in the engine needs a new dataclass field.

`collect_revoice_segments` (`services/processing.py:126-164`) then chooses between two
branches: when the notes match the joined segment text it copies the segment dicts whole
(`:151-152`, `chosen = [dict(s) for s in segs]` — overrides ride along), and when they do
not it re-cuts the text with `_split_sentences` and spreads it by character length over the
section window (`:153-154`, `_spread_over_window:108-123`) — **overrides are lost**. The
translated path always takes the second branch, because `services/revoice.py:49` joins the
*whole* transcript into one string before translating.

### 1.4 What `assemble_master` does with the pins

`services/processing.py:190-214`. Input is an ordered list of `(start_seconds | None,
unused, AudioSegment)`; the middle slot is never read. In synced mode
(`revoice_sync_mode == "synced"`, the only mode anything sets —
`core/project_manager.py:66`, never written by the API):

```
cursor_ms = len(master) or 0
gap = round(start_s * 1000) - cursor_ms
if gap > 0: chunk = silence(gap) + chunk
master = chunk if master is None else master + chunk
```

Consequences to design around: the first chunk's pin becomes leading silence; a negative
pin clamps to 0 by accident; a pin behind the cursor is ignored, so the order the chunks
arrive in is the order they play regardless of their pins; and free mode (`is_free`) drops
pins entirely, which would make this whole feature meaningless — do not expose it.

### 1.5 Already present and reusable

- **`audio.wav` beside every transcribed video** — `services/transcription.py:43` writes it
  via `extract_audio` (`core/video_importer.py:227-273`) with
  `-acodec pcm_s16le -ar 16000 -ac 1` (`:250-258`). Already served at
  `GET /{pid}/tracks/original-audio` (`api/routers/projects.py:344-379`), already deleted
  with the project (`services/projects.py:173-183`).
- **`effective_voice(override, default, provider)`** — `core/tts_provider.py:187-212`,
  already used by the deck path at `services/processing.py:730-734`. It silently falls back
  when the override belongs to the other provider; that is the rule the slide editor
  already relies on and the one to reuse here.
- **A TTS cache keyed on (text, voice, speed)** — `utils/helpers.py:41-52`,
  `core/edge_tts_generator.py:121-133`. "Hear this line" is free on the second press and
  warms the render's cache.
- **`studio_settings.resolve_narration` / `check_voice_for_provider`** —
  `services/studio_settings.py:233-246`, `:196-217`: the same validation the generate and
  re-voice routes use.
- **`kokoro_model_present()`** — `core/kokoro_tts_generator.py:188-199`, a stat-only probe
  that never downloads.
- **The per-pid lock idiom** — `services/slides.py:78-92` (`project_lock` / `forget`),
  already released on delete (`services/projects.py:198-204`).
- **`numpy`** is a declared dependency (`requirements.txt`), and `data/*` is git-ignored
  (`.gitignore:24`) so a cache file inside a project directory neither ships nor dirties the
  installed checkout (`tests/test_no_state_tracked.py`).

### 1.6 One assumption in the brief is wrong

**The peaks endpoint does not need ffmpeg.** `audio.wav` is *our own* file, written as
16 kHz mono PCM s16le (`core/video_importer.py:250-258`), which Python's stdlib `wave`
module reads directly. No subprocess, no ffmpeg, and crucially **no ffprobe** — which may
not exist at all: `utils/config.py:54-59` documents that the imageio-ffmpeg fallback ships
ffmpeg only, so `_FFPROBE_PATH` can be `None` and `core/video_creator.py:260-270`'s bare
`"ffprobe"` call legitimately fails in the packaged app. Decode cost for the 341 s case is
5.5 M int16 ≈ 11 MB, tens of milliseconds with numpy.

ffmpeg becomes necessary only if a waveform of the *narration* (`<stem>_narration.mp3`,
`services/processing.py:85-100`) is ever wanted. Design the module so that path can be
added later; do not build it now.

---

## 2. Data model

Five optional keys **on each transcript segment**, in the outer `project.json`:

```jsonc
{
  "start": 12.340, "end": 15.670, "text": "…",   // as today
  "offset": -0.40,            // seconds added to start when pinning; absent/null = 0.0
  "muted": true,              // absent/null = false — the sentence is not synthesised
  "voice": "en-GB-RyanNeural",// absent/null = the job's voice
  "provider": "edge_tts",     // which provider `voice` belongs to; absent/null = the job's
  "speed": 1.15               // absent/null = the job's speed / the existing per-sentence rule
}
```

**Why on the segment rather than a `transcript_overrides` map keyed by index.** An index
map has the worse failure: re-transcribing changes the indices and the map silently applies
someone's offsets to *different sentences*. On-segment, a re-transcribe wipes the overrides
along with the sentences they belonged to — visible loss instead of silent corruption. It
also keeps "a project is a directory you can zip" (`services/projects.py:1-14`) and needs no
new table, matching the ownership decision in `enterprise-admin.md`.

**An older project has none of the five keys.** Every reader uses
`float(seg.get("offset") or 0.0)` / `bool(seg.get("muted"))` / `seg.get("voice") or None`.
There is no migration, no backfill, and no version flag — absent means default, which is
exactly today's behaviour.

Bounds, in `api/schemas.py`:

```python
class SegmentOverride(BaseModel):
    """PATCH /api/projects/{pid}/transcript/{index}. A field left out is left
    alone; an explicit null clears it back to the default (as SlideUpdate)."""
    model_config = ConfigDict(extra="forbid")

    offset: StrictFloat | None = Field(None, ge=-300, le=300)
    muted: bool | None = None
    voice: str | None = Field(None, max_length=200)
    provider: str | None = None          # validated with check_voice_for_provider
    speed: StrictFloat | None = Field(None, ge=0.5, le=2.0)
```

`StrictFloat` for the same reason `SlideUpdate.pause_override` uses it
(`api/schemas.py:172`): so a boolean is not coerced to 1.0.

`TranscriptSegment` (`api/schemas.py:96-99`) gains **`model_config = ConfigDict(extra="forbid")`
and nothing else** — the whole-list PATCH stays a text editor. Instead,
`services/projects.py::set_transcript` copies the five override keys across by index when
the incoming list is the same length, and drops them (returning the count) when it is not.
One sentence the UI can show: *editing the words never moves the sentences.* One writer per
concern; today's Save button keeps working unchanged.

New module `services/narration.py`, in the shape `services/slides.py` has for the slide
editor: the override vocabulary, validation, `update_segment(pid, index, **changes)`, and
`segments(pid)`.

> **Corrected by what phase 1 actually built.** This paragraph originally put the per-pid
> lock in `services/narration.py`. It is in **`services/projects.py`**, beside the file it
> guards, and `narration` takes `store.project_lock(pid)`. A lock owned by whichever
> feature needed it first protects that writer only from itself — which is exactly the bug
> the Reviewer found and measured. See trap 3.

---

## 3. `_revoice_video` — precisely what changes

All of it in `services/processing.py:862-1077`. Five edits:

1. **`:948-951`** — `spoken` is built from segments with text. Add `and not seg.get("muted")`.
   Filtering *here*, before the window maths, is what makes the sentence **before** a muted
   one inherit its room, and what makes the last-unmuted sentence pick up the
   "runs to the end of the video" rule at `:964-965`.
2. **`:954`** — `seg_start = max(0.0, _seconds(seg.get("start"), sec["start"]) + _offset(seg))`.
   Clamp explicitly rather than relying on `assemble_master`'s accidental floor, so the log
   line at `:1010` and the UI agree.
3. **`:961-965`** — `next_start` must apply the *next* segment's offset too, or dragging a
   sentence later silently shrinks nothing and the squeeze at `:1005` fires on the wrong
   window. Extract a two-line helper `_pin(seg, sec)` and call it in both places.
4. **`:969-971`** — `speed`: when the segment carries an explicit `speed`, use it verbatim
   and **skip `_per_sentence_speed` entirely** (`:839-860`). That is the "no automatic rate
   fitting" rule made concrete: the user's number wins.
5. **`:974-977`** — `voice_id=effective_voice(seg.get("voice"), self.voice_id, self.provider)`,
   exactly as the deck path does at `:730-734`.

Plus two smaller ones:

- **`:1004-1018`** — skip the `_tempo` squeeze for a sentence with an explicit `speed`. The
  user asked for that length; the overrun is absorbed at the next pause (§Headline).
- **`:791-837`** `_calibrate_tts_baseline` samples three sentences at `self.voice_id`. Filter
  muted sentences out of the sample; note in the docstring that the baseline is single-voice
  and only feeds `_per_sentence_speed`, which an explicit speed bypasses.

And one thing that must be fixed *with* this work: the re-voice loop does **not** retry a
failed synthesis (`:973-982` — one attempt, `continue`, a `logger.warning`), unlike the
deck path's three attempts with backoff (`:738-754`). A whole sentence can vanish from the
narration with no evidence outside the server log. Count the failures and return them in the
job result so the UI can say "3 sentences could not be synthesised" — the pattern the AI
loops already use (`services/jobs.py:108-113`).

`services/revoice.py:83-86` must copy the five keys into `original_segments`. Nothing else
in the engine needs to know where they came from.

---

## 4. The waveform

```
GET /api/projects/{pid}/waveform
→ 200 {"buckets": 2728, "bucket_seconds": 0.125, "duration": 341.02,
       "sample_rate": 16000, "peaks": [0…255, …]}
→ 400  not a video project
→ 404  audio.wav absent — reuse the TRACK_KINDS wording at api/routers/projects.py:349
       ("Transcribe the video first - its audio is extracted then.")
```

- **No query parameter.** Resolution is `clamp(round(duration * 8), 800, 12000)` — a fixed
  125 ms per bucket, independent of length, so there is exactly one cache entry per project.
  341 s → 2728 buckets ≈ 11 kB of JSON; a 2-hour recording caps at 12000 ≈ 58 kB. The client
  max-pools down to its pixel width, which is exact and trivial. A `?buckets=` parameter was
  considered and rejected: it multiplies cache entries for no visual gain.
- **One magnitude per bucket** (max `|sample|` scaled to 0–255), not a min/max pair: mono
  speech drawn as a symmetric strip gains nothing from the second value and the payload
  doubles.
- **Cache** at `data/projects/<pid>/audio.peaks.json`, holding
  `{"mtime_ns", "size", "buckets", "duration", "sample_rate", "peaks"}`. Hit when the source
  file's `mtime_ns` + `size` match. `data/*` is git-ignored (`.gitignore:24`) and
  `delete_project` removes every child before the record (`services/projects.py:173-190`),
  so the cache needs no new cleanup path.
- **Read with `wave` + numpy in chunks** (say 1 M samples) so a long recording never loads
  whole. New module `services/waveform.py`. A plain `def` route, not `async def`: FastAPI
  runs sync endpoints in its threadpool, and every existing route here is sync.
- **Duration comes from the WAV header** (`nframes / framerate`), not from ffprobe and not
  from `record["duration"]`. The strip and the blocks must share one scale, and it must be
  the scale of the file actually being drawn.

**Content-security constraints — the honest position.** There is no CSP header anywhere:
`api/app.py` sets none, and the desktop shell explicitly disables it
(`desktop/src-tauri/tauri.conf.json:26`, `"csp": null`). The real constraint is the
dependency and offline posture: `frontend/package.json:15-23` is seven runtime packages, and
CLAUDE.md's frontend conventions say hand-authored CSS, lucide icons, no UI kit. So:

- **No wavesurfer.js / peaks.js / any audio library.** Adding one also fails
  `tests/test_frontend_dist.py` until `dist` is rebuilt, because `package.json` and
  `package-lock.json` are both inside the fingerprint (`:24`).
- **No `decodeAudioData` in the browser.** It would need the audio bytes fetched through the
  session cookie, and a 2-hour `audio.wav` is 115 MB. **This is about the SOURCE audio and
  nothing else** — the per-sentence TTS clips the audition plays are ~20 kB each and are
  decoded in the browser, which is the whole of phase 3a. See the first of phase 3a's notes in
  §7 before "fixing" either half of this.
- **Draw one SVG `<path>`**, not 2728 rects: `viewBox="0 0 N 100" preserveAspectRatio="none"`
  with a single filled path mirrored about the centre line. One DOM node, scales with CSS,
  no canvas ref, no resize observer.
- `frontend/index.html:7-9` already fetches Google Fonts from a CDN — that is the one
  external request in the app. Do not add a second.

---

## 5. Hear one sentence

**Nothing does this today.** `services/voices.py` lists voices and nothing else; the only
"preview" in the codebase is the 15-second *video* render (`api/schemas.py:146-147`,
`ProjectDetail.tsx:347-354`). There is no synthesis route at all.

```
GET /api/projects/{pid}/transcript/{index}/preview?provider=&voice=&speed=
→ 200 audio/mpeg (FileResponse from the TTS cache path — which is where the clip is READ
      from, never where the provider is pointed; see §7 phase 2)
→ 400 unknown provider, a voice belonging to the other one (resolve_narration), a speed out
      of range, a deck or PDF, or a sentence with NO WORDS IN IT — the text editor can save
      an empty line and there is then nothing to synthesise; this list omitted that case and
      the build had to add it
→ 404 no such project / no transcript / index out of range
→ 409 Kokoro's model is not on disk — the shared first sentence of services/voices.py's
      notice plus what to do about it, NOT that notice verbatim: its "This is the built-in
      voice list." is untrue of a Play button, which is why the constant was split
→ 502 the provider returned nothing, or nothing inside the bounded wait
```

Defaults are **the segment's own overrides, else the studio defaults**, so a parameterless
call means "play this line the way the render will". The text comes from the stored
transcript, not the request body.

**Why GET.** It keeps the route out of the audit guard honestly — `tests/test_audit.py:259-260`
counts every non-GET as mutating — and it makes the browser's own cache work. The trade-off
is that you can only preview what is **saved**; the transcript editor already has an explicit
Save, so that is a reasonable rule. If a draft preview is ever wanted it becomes a POST and
must be added to `AUDIT_EXEMPT` in writing, in the shape of the existing `ai_enhance_one`
entry (`tests/test_audit.py:47-54`): *"synthesises one sentence as a preview; saves nothing."*

**What it costs.** Edge TTS is a network round trip, cached by `(text, voice, speed)`
(`utils/helpers.py:41-52`) — so a repeat press is a file copy and the preview *warms the
render's cache*. **Measured on the development machine, 2026-09-15: ~1 s for a sentence once
the process is warm, and ~10 s for the very first press after the server starts** (the
`edge_tts` import plus the first connection). The 1–3 s this section originally estimated is
the warm case only; a cold start is an order of magnitude worse, which is what the bounded
wait below has to be sized against (shipped: 30 s) and what the UI has to say while it waits,
or the first press looks like a broken button. Two hazards:

- `_run_async`'s ceiling is **120 s** (`core/edge_tts_generator.py:20-28`), and
  `generate_audio` swallows every exception and returns `None` (`:159-161`). A hung request
  holds a threadpool thread for two minutes. Bound the wait explicitly and map `None` to a
  readable 502, never a 500.
- Kokoro's first synthesis downloads ~340 MB (`services/voices.py:28-31`). Probe with
  `kokoro_model_present()` (`core/kokoro_tts_generator.py:188`) and answer 409 rather than
  blocking.

Cap the previewed text at ~1000 characters. Note that `data/cache` is never swept: a session
that auditions fifty sentences across five voices leaves 250 files and no purge endpoint
exists.

Play it with a plain `<audio src="/api/…">` — same-origin cookies ride along, which the
existing `<video src="/api/projects/…/video">` (`ProjectDetail.tsx:504`) already proves.

---

## 6. The UI — extend, don't replace

Today the transcript editor is `ProjectDetail.tsx:522-556`: a Card holding one `Textarea`
per segment with an `m:ss` label (`:530-539`) and a Save button (`:541-543`), over
`segments` state (`:94`, synced at `:114-116`, saved at `:203-206`). The Re-voice card is
`:558-648`, including the Separate tracks block (`:612-644`) that already downloads the
picture and both audio tracks.

**Extend, in a new component.** Extract the transcript into
`frontend/src/components/project/TranscriptCard.tsx` and give it two views over the *same*
`segments` state, switched with the existing `Tabs` primitive (`components/ui.tsx:131-142`):

- **List** — exactly what exists today, plus an offset number input and a mute checkbox per
  row. This is where a Whisper mis-hearing gets fixed; the timeline does not replace it.
- **Timeline** — the waveform strip with one block per sentence.

Rationale: `ProjectDetail.tsx` is already 651 lines and `SlidesCard.tsx` (983 lines) is the
established precedent for exactly this extraction. Pure helpers go in
`frontend/src/lib/timeline.ts` with `timeline.test.ts`, matching `lib/slides.ts` /
`lib/slides.test.ts` — the vitest config only picks up `src/**/*.test.ts`
(`vite.config.ts`), and there is no `frontend/e2e/` directory despite CLAUDE.md naming
Playwright, so unit tests on pure helpers are the only automated frontend coverage available.

Timeline behaviour:

- Blocks sit at `start + offset` with width `end - start` — the **original** speech length,
  which is what lines up with the waveform burst underneath. The gap to the next block *is*
  the slack, visible as empty space.
- A ghost outline at the original position whenever an offset is set, so "what I moved" is
  visible without a second panel.
- Drag to offset, with a numeric nudge (±0.05 / ±0.25 s buttons and a typed field) for
  precision and for keyboard users. The drag is the convenience; the number is the contract.
- The selected block gets mute / voice / speed / **Play** (§5) and a **Reset** that sends
  all four fields as `null`.
- Blocks are **not** re-sorted when one is dragged past its neighbour — the pipeline never
  re-sorts (`group_by_section:167-187`, `spoken:948-951`) and they will simply play
  consecutively. Show an overlap marker; do not silently reorder the script.
- Write on drag **end**, not per frame (see trap 3), one `PATCH …/transcript/{index}` per
  committed change.

`frontend/src/lib/format.ts:37-42` `duration()` rounds to whole seconds and is used by the
Details card and the transcript list. Add a *separate* `timecode(seconds)` returning
`m:ss.mmm`; do not change `duration()`.

API surface (new router `api/routers/narration.py`, `prefix="/projects"`, registered in
`api/routers/__init__.py:6-12` — following `api/routers/slides.py`'s precedent rather than
growing `projects.py`):

| Route | Notes |
|---|---|
| `PATCH /{pid}/transcript/{index}` | `require_project` + `jobs.require_idle`; audits a **new** constant `PROJECT_TRANSCRIPT_TIMING = "project.transcript_timing"` added to `api/audit.py:82-91` `ACTIONS`; detail = field names only, never the text (`api/audit.py:128-134`) |
| `GET /{pid}/waveform` | §4 |
| `GET /{pid}/transcript/{index}/preview` | §5 |

Three new `{pid}` routes for `tests/test_project_ownership.py:32-63`; one new mutating route
for `tests/test_audit.py`.

---

## 7. Build order

**Phase 1 — overrides the render honours (small, and it fixes the reported problem).**
**DONE**, 2026-09-15; see CHANGELOG `#narration-overrides`, `#project-record-lock` and
`#revoice-failures`. `SegmentOverride`'s five fields in `api/schemas.py`; `extra="forbid"`
on `TranscriptSegment` plus the merge in `set_transcript`; `services/narration.py` (the
override vocabulary, the validation, `update_segment` / `segments` / `count_spoken`);
`PATCH /{pid}/transcript/{index}` in the new `api/routers/narration.py`; the five edits in
`_revoice_video` (§3) plus the muted-sentence filter in `_calibrate_tts_baseline`; an offset
box and a Mute checkbox in the **existing** list view, with `timecode()` added beside
`duration()` in `frontend/src/lib/format.ts` (§6). No waveform, no drag, no new component
tree — as scoped. The owner can nudge the one sentence that is wrong and re-voice. Tests:
`tests/test_narration_overrides.py` (25 — route, validation, the merge rule, the race and
the atomic write), `tests/test_revoice_sync.py` (+13, on the pydantic-free fake-segment
harness exactly as expected), `tests/test_revoice.py` (+3), vitest `format.test.ts` (+2).

Five things the build changed about the design above. Read them before phase 2 or 3, because
four of them contradict a section or a trap that still reads as written:

- **The lock is NOT in `services/narration.py`** (§2, trap 3 both say it is). It is
  `services.projects.project_lock`, beside the file it guards, because `set_transcript` is a
  read-modify-write of the SAME record — a lock owned by this feature would have protected
  this feature from itself and from nothing else. Every read-modify-write of the outer
  `project.json` takes it and `delete_project` drops it, `forget`-style. A new writer of this
  record takes it from `services/projects.py`, never from a sibling.
- **`save_project` had to become atomic** — a temp file plus `os.replace`, with a retry for
  Windows' transient sharing refusal on the rename (2 failures in 60 concurrent runs before
  it, 0 after). The collision above did not merely lose an adjustment: it tore
  `project.json`, and `get_project` answers None for a record it cannot parse, so the project
  vanished from the list and 404'd on every route with its files still on disk. Trap 3 named
  the interleaving; it did not name the tearing.
- **The merge matches the sentence's window, not only its index.** §2 says index-wise when
  the incoming list is the same length — but length is not identity: a Save that deletes one
  sentence and adds another keeps the count, and an index-only rule slides every adjustment
  down a row, which is the silent corruption an index-keyed map was rejected for in the first
  place. Shipped as index **and** the same `start`/`end`; the count that could not be carried
  comes back as `timing_adjustments_dropped` and the page shows it.
- **Trap 4 applies to the whole-list PATCH too**, not only to the new routes: it merges the
  stored adjustments now, so `PATCH /{pid}/transcript` takes `jobs.require_idle` as well.
  Trap 2's "re-read immediately before saving" was applied to the two existing job writers
  while the reason was fresh — `services/transcription.py` and `services/revoice.py` both
  save onto the record as it is when they finish.
- **Traps 7 and 9 were closed here rather than later**, and 9 grew a third case: a sentence
  whose offset pins it at or past the end of the video is counted as a failure too, because
  the mux is `-shortest` and it would not be in the render at all. Trap 8 is answered in the
  UI — the re-voice card says the adjustments do not apply to a translated re-voice — rather
  than by refusing the combination.

Left for phase 2, deliberately: the per-sentence **voice** and **speed** are stored,
validated and honoured by the render, but neither has a control in the UI yet.

**Phase 2 — per-sentence voice and speed, and hear one sentence.** **DONE**, 2026-09-15; see
CHANGELOG `#sentence-preview`, `#preview-atomic-cache` and `#preview-errors`. The two fields
phase 1 stored gained controls on **every** row of the existing list — a Speed box and a
type-ahead Voice box beside the offset and the mute — plus a **Play** button per row over the
new `GET /{pid}/transcript/{index}/preview` (`api/routers/narration.py` beside the PATCH,
`narration.preview_segment` / `_synthesise` / `cache_path_for` underneath). `services/voices.py`
split its Kokoro sentence into `KOKORO_MODEL_PENDING` so the 409 and the voice-list notice
say the same thing; `services/projects.py`'s rename helper became public
(`replace_with_retry`); `frontend/src/lib/narration.ts` + `narration.test.ts` hold the pure
rules; `api.blob` was added to `frontend/src/api/client.ts`; the row controls are in
`frontend/src/pages/ProjectDetail.tsx`. No waveform, no drag, no timeline strip, no new
component tree — as scoped. Tests: `tests/test_narration_preview.py` (29), vitest
`narration.test.ts` (10), `tests/test_project_ownership.py` (+1 route in the sweep).

Six things the build changed about the design above. Read them before phase 3, because four
of them contradict a line that still reads as written (§5's own errors were corrected in
place instead, since the next reader will rely on them):

- **The provider must never write to the cache path.** §5 originally read "FileResponse
  straight from the TTS cache path"; that path is where the clip is *read* from, and it has
  been corrected above. Pointing `generate_audio` at it (its no-`output_path` default) hands
  the provider the shared cache entry to stream a download into, and the only completeness
  check anywhere is that the file exists. That is a half-written file served to a second
  press, and — worse — a dropped stream leaving a truncated entry at that key forever, read
  by every later preview and copied out by every later **re-voice**, which counts it a success
  and muxes the stump into the video, straight past phase 1's failed-sentence counting.
  Shipped as a private `.part` file per press published with an atomic rename
  (`services.projects.replace_with_retry`, shared with `save_project` rather than copied), an
  empty result refused, and a zero-byte entry already at the key removed and remade.
  **Phase 3 inherits the rule:** anything that hands a generator a path must hand it a
  private one.
- **A plain `<audio src="/api/…">` was the wrong instruction** (§5's last line). It works —
  the cookie rides along — but a media element is told only that its source failed, so every
  message this route writes (the 409, the voice-mismatch 400, the 502 naming the provider)
  was replaced by a guess, and pressing Play is exactly how someone finds out a hand-typed
  voice id is wrong. Fetched with `credentials` and played from a blob instead, which also
  bounds it to one request in flight: a second press is ignored rather than starting a second
  synthesis of the same sentence.
- **The provider is never taken from the segment.** A stored `provider` exists only so
  `effective_voice` knows which provider the stored *voice* belongs to; `_revoice_video`
  resolves the engine from the job, else the studio. Reading it here would make the preview
  diverge from the render in the one case it looked helpful — a Kokoro-voiced sentence under
  an Edge studio previewing in Kokoro and rendering in Edge.
- **The voice control is one shared `<datalist>`, not a `<select>`** (§6 assumed the controls
  sit on a *selected* block, where the size of the list does not matter). On a per-row
  control it does: north of 300 voices (322 on Edge today) × one row per sentence is tens of
  thousands of `<option>` nodes on a ten-minute video. A `<datalist>` is one list for every
  input and filters as you type; a voice picked off it saves at once, while a hand-typed one
  waits for the blur.
- **The controls are on every row, and there is no Reset yet.** §6 puts mute / voice / speed /
  Play on the selected timeline block; with no timeline in phase 2 they went on each list row
  instead, and the per-segment **Reset** (§9's "enough for v1") is still unbuilt. §6's
  extraction into `TranscriptCard.tsx` did not happen either — the transcript is still inline
  in `ProjectDetail.tsx`, which phase 3 should extract as planned — and the pure helpers went
  to `lib/narration.ts` rather than `lib/timeline.ts`, since they are the editor's rules
  rather than the strip's.
- **The preview does NOT take `jobs.require_idle`** (trap 4 and §6's table put it on all three
  new routes). It writes nothing, and refusing a listen while a re-voice runs would be a 409
  on a read; the PATCH still takes it. One consequence to keep: `SegmentNotFound` is answered
  **404 by the preview and 400 by the PATCH**, and both are right — a GET of something that is
  not there, against a write whose index argument is out of range.

Trap 19 stands and is now load-bearing: `data/cache` is still never swept, and previewing is
the cheapest way yet to fill it. Accepted, not fixed — the render shares those entries and
this route must not delete behind it.

**Phase 3 — the waveform and the timeline strip. This is the expensive phase.** The peaks
endpoint is genuinely small (~80 lines plus its cache). The cost is the timeline component:
drag with pointer capture, seconds↔pixels with a zoom level, max-pooling the peaks to the
pixel width, block hit-testing, keyboard equivalents for every mouse gesture, a
non-reflowing render for 1500 blocks on a 2-hour recording, and the `frontend/dist` rebuild.
Budget it as more than phases 1 and 2 combined and do not start it until phase 1 is in the
owner's hands. **It was split in two when it was built**: 3a is the strip and the audition,
3b is the drag.

**Phase 3a — the strip, and auditioning the narration without rendering it.** **DONE**,
2026-09-16; see CHANGELOG `#narration-timeline` and `#tts-cache-publish`. The transcript
finally moved out of `ProjectDetail.tsx` into
`frontend/src/components/project/TranscriptCard.tsx` (§6's extraction, deferred by phase 2)
with a **List / Timeline** switch over the *same* `segments` state: List is phase 1 and 2's
row editor unchanged, Timeline is the new
`frontend/src/components/project/NarrationTimeline.tsx` — a client-side filmstrip (a hidden
`<video>` seeked and drawn to a canvas; no ffmpeg, no server work), the waveform strip, and
one block per sentence at `pinned_start` with width `end - start`, plus a ghost outline at
the original position whenever an offset is set. `GET /{pid}/waveform` and `services/waveform.py`
are §4 as designed — stdlib `wave` + numpy, chunked, 125 ms buckets, cached on the source's
`mtime_ns` + size beside it, one SVG `<path>` — and `GET /{pid}/narration/plan` is a route §6's
table does not have at all (see the notes below). The pure helpers are
`frontend/src/lib/timeline.ts` / `timeline.test.ts` as §6 named them. Server-side the render's
rate rules were pulled to module level in `services/processing.py`
(`sentence_window`, `per_sentence_speed`, `sentence_speed`, `calibrate_tts_baseline`,
`DEFAULT_BASELINE_RATE`, `SQUEEZE_TOLERANCE`, `SQUEEZE_MAX_FACTOR`) with the old
`VideoProcessor` methods kept as delegating seams, and `services/narration.py` grew `plan`,
`baseline_rate`, `transcript_section`, `preview_url` and `forget_baseline`.
`replace_with_retry` moved to `utils/helpers.py` beside the new `publish_to_cache`, which
both TTS generators now use. Tests: `tests/test_narration_plan.py` (42),
`tests/test_waveform.py` (26), `tests/test_tts_cache_publish.py` (8),
`tests/test_project_ownership.py` (+2 routes), vitest `lib/timeline.test.ts` (34) — **603
backend and 103 vitest across the suites**, `tsc --noEmit` clean. Deliberately left: **the
drag** (phase 3b — offsets are still typed in the List view), the per-segment **Reset**
(§9's "enough for v1", still unbuilt since phase 2), a **playhead that scrubs the video from
the strip** beyond clicking a block to seek (§9), and any waveform of the *narration* (§4,
§9 — it needs the ffmpeg decode path and it would be a predicted waveform before a render).

Seven things the build changed about the design above. Read them before phase 3b, because
several contradict a line that still reads as written:

- **§4's "no `decodeAudioData` in the browser" does not mean what it looks like it means,
  and the audition depends on that.** It was written about the *source* `audio.wav` — 115 MB
  for a two-hour recording — which is why the waveform is drawn from server-side peaks and
  always will be. The clips the audition plays are **per-sentence TTS mp3s, about 20 kB
  each** (the whole narration of a 5m41s video is 1.3 MB), and they **are** fetched and
  `decodeAudioData`'d in the browser: Web Audio is the only way to place a clip on a shared
  clock to the millisecond, and a pile of `<audio>` elements is not a substitute. §4's bullet
  now carries a pointer to this note. Do not "fix" it.
- **The plan endpoint is not in §6's table, and it is the load-bearing piece.**
  `GET /{pid}/narration/plan` exists so the render's rate rules are never reimplemented in
  TypeScript. **The server owns what each sentence says and how fast; the client owns only
  where the clips land.** The plan answers, per sentence, the effective voice and speed, the
  window, whether it is squeezable, whether it is speakable at all, and a `preview_url`
  carrying that effective pair — from the very functions `_revoice_video` calls, which is why
  they were pulled to module level with the methods kept as delegating seams (the sync tests
  monkeypatch `VideoProcessor._per_sentence_speed` on the class and `_calibrate_tts_baseline`
  on the instance, and `_revoice_video` still calls both through `self`, so those patches
  land exactly as they did). Two costs the design did not anticipate, both closed here: the plan
  **measures the speaking rate**, which is three syntheses, so it is bounded at 45 s
  (`BASELINE_TIMEOUT_SECONDS`, above the preview's 30 s because the first of the three pays
  the same ~10 s cold start) and falls back to the engine's default rather than holding a
  request; and it is **memoised per (project, provider, voice)** and dropped by
  `forget_baseline` whenever the transcript changes or the project is deleted, because a tab
  reopened past react-query's `staleTime` or a change in the Re-voice card re-fetched the
  plan and used to re-pay the calibration each time.
- **The audition must model the post-synthesis tempo squeeze, or it lies in exactly the place
  it is supposed to help.** §6 describes blocks and slack and nothing about playback, so
  nothing in it says this. Without the squeeze, a clip that runs more than `SQUEEZE_TOLERANCE`
  (1.15) over its window plays at full length, pushes the next clip late, and the audition
  shows a cascade through every sentence after it — while the render would have sped that one
  clip up with `atempo` to fit exactly (unless the factor exceeds `SQUEEZE_MAX_FACTOR`, 2.0,
  past which speech stops being intelligible and the overrun is let through). Both constants
  are **named on the server and ride in the plan payload** rather than being written into the
  client, because a second copy of the numbers would be free to drift from the loop it exists
  to predict. One approximation is accepted and written down beside it: the render trims each
  clip's leading silence before measuring, the browser holds the untrimmed mp3, so the
  prediction fires a hair early and never late.
- **A URL is not a sufficient cache key for an audition.** The obvious signature for "are the
  decoded clips still the right ones" is the list of `preview_url`s, and it is wrong.
  `preview_url` carries the voice and the speed but **not the text**, and it cannot be made
  to: `per_sentence_speed` floors at the job's speed, so a sentence whose narrator was slower
  than the TTS baseline sits exactly on that floor and its URL does not move however the words
  are changed — and neither does the URL of any sentence carrying an explicit per-sentence
  speed. Signed on the URL alone, fixing a Whisper mis-hearing left the **old** clip in the
  buffer, played it back, and — because its decoded duration feeds the schedule — mis-placed
  every sentence after it too. The text is in the signature.
- **`replace_with_retry` moved to `utils/helpers.py`** (phase 1's note and trap 3 both put it
  in `services/projects.py`, which now re-exports it so every caller and every test that
  patches `store.replace_with_retry` is unaffected). The reason is the layering, and it is not
  negotiable: the TTS generators live in `core/`, and **`core/` may import `utils/` but must
  never import `services/`** — the engine is the layer underneath. It moved because phase 2's
  rule ("anything that hands a generator a path must hand it a private one") turned out not to
  go far enough: the generators themselves mirror a finished clip onto the shared cache key,
  and both did it with a plain `shutil.copy`, so an interrupted copy left a truncated entry at
  that key **permanently** — served by every later preview and copied out by every later
  re-voice, which checks only that a file exists. `utils.helpers.publish_to_cache` (temp file
  plus `replace_with_retry`, never raises) is that fix, and it is pre-existing *render*
  behaviour rather than anything the timeline introduced; what the timeline changed is that a
  browser GET now drives the same path concurrently with previews and a running re-voice.
- **The last sentence's end bound: ffprobe here, the WAV header there — accepted, not
  unified.** `_revoice_video` bounds its final spoken sentence by `_probe_duration(video)`
  and drops any sentence pinned at or past it; the plan uses the WAV header's duration
  (trap 5 forbids ffprobe for the timeline, trap 6 forbids spawning anything at all). When
  ffprobe exists the two are the same recording measured two ways and agree to within a
  frame. When it does not — which the packaged app must assume — the render gets
  `video_end = 0.0` and falls back to the *section's* end while the plan still uses the WAV
  duration, and those differ whenever the recording runs on after the last word. Blast
  radius: the last spoken sentence only, and only when the bound would make it short enough
  to be sped up. Documented in `services/narration.plan` rather than papered over.
- **A sentence pinned past the end of the video is marked, not auditioned**, and the *server*
  decides which sentences are speakable at all. The `-shortest` mux drops such a sentence
  (phase 1's third failure case), so playing it would be auditioning audio the render cannot
  produce; the plan returns `speakable` and `past_end` and the client never re-derives either.
  That is not fussiness: "has words" is `str.strip()` on the server and would be
  `String.trim()` in the browser, and those are different character sets (U+001C–1F strip but
  do not trim; U+FEFF trims but does not strip), so a pasted control character would be
  auditioned by the browser and refused 400 by the preview route — a per-sentence failure with
  no cause anybody could see.

**Phase 3b — drag.** Not built. §6's drag bullet, the ±0.05 / ±0.25 s nudges and the keyboard
equivalents all still read as written and are all still outstanding, as is trap 3's surviving
half: **commit on drag end, not per frame** (one `PATCH …/transcript/{index}` per committed
change). Everything it needs is in place — `lib/timeline.ts` already owns seconds↔pixels and
the zoom, the blocks are already positioned from `pinned_start`, and the audition already
re-plays from the plan whenever an adjustment invalidates it — so 3b is the gesture, its
keyboard equivalent, the hit-testing and the write, not a new data path.

**Phase 4 (optional) — per-segment translation.** `core/translator.py:96-123`
`translate_notes` already returns a list the same length as its input with failures passing
through unchanged, so translating segment-by-segment instead of `services/revoice.py:49`'s
one joined string preserves the 1:1 mapping and makes overrides meaningful for a translated
re-voice. The cost is N Ollama calls instead of one — on a 100-segment transcript at ~2 s
each that is a job with a progress bar, not a request.

---

## 8. Traps

1. **Today's Save button will delete every override.** `TranscriptSegment`
   (`api/schemas.py:96-99`) has no `model_config`, so pydantic's default `extra="ignore"`
   drops unknown keys; `set_transcript` (`services/projects.py:212-219`) then writes the
   stripped list back wholesale, and `ProjectDetail.tsx:541-543` posts the whole array on
   every Save. Ship `extra="forbid"` and the index-wise merge **in the same change** as the
   fields, never after.
2. **Two writers save a stale copy of `project.json`.** `transcribe_project` reads at
   `services/transcription.py:23` and saves at `:57`; `revoice_project` reads at
   `services/revoice.py:29` and saves at `:117`. Generate deliberately re-reads first
   (`api/routers/projects.py:240-241`, with the comment explaining why). Every new writer
   must re-read immediately before saving — and re-transcribing *legitimately* discards
   overrides, so the UI must warn before it starts, not after.
3. **The outer `project.json` lock — CLOSED by phase 1, and read this before phase 3.**
   It originally said to add the lock in `services/narration.py`. Do not: a lock owned by
   one feature guards that feature only. `services/projects.py` owns `project_lock(pid)`
   and `forget(pid)` now, beside `project.json`; `set_transcript`, `narration.update_segment`
   and `delete_project` all take it, and `save_project` writes atomically (temp file plus
   `os.replace`, with a retry — `os.replace` intermittently fails on Windows while anything
   holds either file for the instant of the rename, measured at 2 in 60 concurrent runs).
   The two job writers deliberately do NOT take it: they re-read immediately before saving,
   and `jobs.require_idle` refuses an edit while a job holds the project. **Phase 3b still
   has to commit on drag end, not per frame** — that was the other half of this trap, phase
   3a shipped no drag at all, and it is the half still outstanding.
4. **The transcript PATCH does not take `jobs.require_idle`.** Every slide write does
   (`api/routers/slides.py:42-47`). The new routes must, which adds a 409 the UI has to
   render — `api/app.py:72-74` already maps `ProjectBusy` to it.
5. **`ffprobe` may not exist.** `utils/config.py:54-59`: the imageio fallback ships ffmpeg
   only, and `core/video_creator.py:260-270` shells out to a bare `"ffprobe"`. When it fails,
   `video_end = 0.0` (`services/processing.py:906`) and the **last** sentence is bounded by
   its section end instead of by the video — so the last block's room in the timeline and in
   the render disagree. Never use ffprobe for the timeline's length; use the WAV header.
6. **Three engine call sites shell out to a bare `"ffmpeg"` / `"ffprobe"`**
   (`core/video_creator.py:286`, `:264`; `services/processing.py:937`) instead of
   `FFMPEG_PATH`. They only work because `utils/config.py:70-78` prepends the ffmpeg
   directory to `os.environ["PATH"]` at import time. Do not copy that pattern; the peaks path
   should spawn nothing at all.
7. **Muting everything fails with a bare "Re-voice failed."** `_revoice_video` returns False
   on empty `aligned_chunks` (`services/processing.py:1028-1030`) and
   `services/revoice.py:101-102` turns that into `RuntimeError("Re-voice failed")`. Refuse at
   the route with a 400, the way an empty transcript already is
   (`api/routers/projects.py:279-280`).
8. **A translated re-voice throws the per-sentence timing away.** `services/revoice.py:49`
   joins the whole transcript, so `collect_revoice_segments` takes the `_spread_over_window`
   branch (`services/processing.py:153-154`), re-cuts the text with `_split_sentences` and
   spreads sentences by character length across the *entire* video. No 1:1 mapping survives.
   Until phase 4, either refuse the combination or say plainly in the UI that adjustments do
   not apply to a translated re-voice.
9. **A failed synthesis silently drops a whole sentence.** `services/processing.py:973-982`
   logs a warning and `continue`s — no retry, unlike the deck path's three attempts
   (`:738-754`). With per-sentence voices this gets more likely, not less. Count and report.
10. **`_calibrate_tts_baseline` measures one voice** (`services/processing.py:791-837`, uses
    `self.voice_id`) and may sample a muted sentence. Filter, and remember the baseline only
    feeds `_per_sentence_speed`, which an explicit speed skips.
11. **`revoice_sync_mode` is always `"synced"`** (`core/project_manager.py:66`; nothing in the
    API writes it). In free mode (`services/processing.py:890-891`) nothing is pinned and the
    entire timeline means nothing. Do not expose it; if it ever ships, the timeline greys out.
12. **`data/projects/<pid>/revoice/` is rebuilt on every run** (`services/revoice.py:79-90`).
    Nothing durable may live there.
13. **`GET /{pid}` returns the whole record** (`api/routers/projects.py:114-116`). Four new
    keys per segment is ~30% more transcript payload; fine at 100 segments, and the reason the
    timeline must not re-render all of them per drag frame at 1500.
14. **A preview must not be a job.** `services/jobs.py:183-197` allows one job per project, so
    a preview-as-job would block the re-voice the user is about to start.
15. **`frontend/dist` is committed and fingerprinted.** `tests/test_frontend_dist.py:23-46`
    recomputes the hash over `src`, `public`, `index.html`, **`package.json`,
    `package-lock.json`**, `vite.config.ts` and both tsconfigs — so even a dependency bump with
    no source change needs `npm run build` and a commit of `frontend/dist`. *(Known.)*
16. **The ownership sweep is checked against the live route table.** All three new `{pid}`
    routes go into `PROJECT_SCOPED_ROUTES` (`tests/test_project_ownership.py:32-63`) with a
    body that passes validation, or the companion test fails. *(Known.)*
17. **Every mutating route audits or is exempted in writing.** `tests/test_audit.py:39-55`,
    `:259-290`; the guard parses the endpoint's source, and a *new named constant* in
    `api/audit.py:82-91` is required — an inline string is rejected. *(Known.)*
18. **Nine version carriers plus both npm lockfiles** (`tests/test_version.py:29-79`), and
    `[Unreleased]` must sit above the release header (`:94-100`).
19. **`data/cache` is never swept.** Previews and renders share it
    (`utils/helpers.py:41-52`); there is no purge endpoint and nothing bounds its growth.

---

## 9. What NOT to build

- **Anything that guesses the right speed.** No "fit to original", no global stretch, no
  auto-tempo, no second attempt at rate matching. `_per_sentence_speed`
  (`services/processing.py:839-860`) stays exactly as it is — speed-up only, never below the
  user's speed, capped at +30% — and an explicit per-sentence speed bypasses it and the
  post-synthesis squeeze entirely. This is settled; it is not a design question to reopen.
- **Picture editing** — cutting, trimming or reordering the video. `_revoice_video` muxes
  with `-c:v copy` (`core/video_creator.py:289`), which is why a re-voice takes seconds
  rather than a full encode. A picture edit forfeits that; it is now its own vertical (`docs/porting/edit-timeline.md`), which measured the re-encode cheap enough to do on every render.
- **Multi-track compositing.** `replace_video_audio` (`core/video_creator.py:300-371`) takes
  one background music path, loops it to length and overlays it at a flat volume
  (`:327-342`) — no ducking, no envelopes. A mixer with tracks is its own vertical. That
  vertical is now **E4 — the music lane** of the edit timeline (`docs/porting/edit-timeline.md`
  §7), sequenced after E2; mixing or ducking the *original* audio stays out.
- **A predicted waveform of the new narration.** The synthesised length of a sentence is
  unknown until it is synthesised, and estimating it is rate-fitting wearing a hat. The
  blocks show the **original** speech windows; the only truthful narration waveform is one
  drawn from `<stem>_narration.mp3` *after* a render, and that is a later, optional addition
  (it needs the ffmpeg decode path §4 defers).
- **Word-level editing or re-timing inside a sentence.** Whisper word timestamps exist
  (`core/subtitle_generator.py:112-219`) but the transcript stores sentence segments only, and
  the re-voice synthesises per sentence. Sub-sentence editing has no consumer.
- **Undo history on the timeline.** The slide editor's 20-step history is the engine's
  (`core/project_manager.py:41`); the transcript has none. A per-segment **Reset** (all four
  fields to `null`) is enough for v1.
- **A playhead that scrubs the video in sync with the timeline.** Tempting and genuinely
  useful, but it is a media-synchronisation problem of its own. At most, phase 3 may seek the
  existing `<video>` element when a block is clicked.
- **Free-pace mode.** See trap 11.

---

### Critical files for implementation

- `services/processing.py` (`collect_revoice_segments:126-164`, `assemble_master:190-214`, `_per_sentence_speed:839-860`, `_revoice_video:862-1077`)
- `services/projects.py` (`set_transcript:212-219`, `save_project:207-209`, `delete_project:146-204`)
- `api/routers/projects.py` (transcript PATCH `:156-165`, revoice `:268-292`, tracks `:344-379`)
- `api/schemas.py` (`TranscriptSegment:96-103`, `SlideUpdate:157-174` as the shape to copy)
- `frontend/src/pages/ProjectDetail.tsx` (transcript card `:522-556`, re-voice card `:558-648`)
