# Design spec — the edit timeline (vertical 6)

Read-only survey, 2026-09-16, plus measurements taken that day on the real 5m41s 1080p
source (`data/projects/6d808448772c/finished.mp4`). **E1 — the model and the render — is
built (2026-09-16, §7); E2 onward is not.** This is the answer to "build something like
Camtasia": a multi-track timeline on which the picture, the original audio and the
narration can be cut together. The survey is written against the code as it stands at
`4f39ae1`, and every line number below is from that tree; E1's own notes in §7 cite symbols
rather than lines, and where E1 proved a section wrong the section is corrected in place
and says so.

The one-line version: **a project gains an ordered list of the ranges of its one source
that are kept; the transcript never moves; everything else is projection.**

## Headline

Camtasia is two products. Its timeline cuts material on tracks; its companion Audiate
transcribes narration and cuts the *words*. Media Studio is already the second half —
transcript editing, per-sentence voice and speed, TTS re-voice, and since phase 3a an
audition of the whole narration against the picture without a render. What it does not
have is the first half: material you can cut. This vertical adds it, and nothing else.

Four facts, each measured or cited, shape the whole design:

- **The transcript's identity is its window, so the transcript must never move.**
  `set_transcript` decides that two entries are the same sentence when they have the same
  `start` and the same `end` (`services/projects.py:303-316`), and carries a sentence's
  adjustments across only onto a match. Cut ten seconds out of the picture the obvious
  way — shift every later sentence earlier — and every adjustment on every later sentence
  is dropped by construction, surfaced as `timing_adjustments_dropped`. That is phase 1's
  corruption class, inverted. So the transcript stays in **source seconds** on disk, exactly
  as Whisper wrote it, and the edit is applied to it as a **projection** computed at plan
  time and render time. Nothing on disk changes coordinates; nothing is ever dropped.
- **One source per project is load-bearing, not incidental.** There is no route that adds a
  second media file; every consumer resolves `PROJECTS_DIR / pid / record["source_filename"]`
  (`services/transcription.py:29`, `services/revoice.py:51`, `api/routers/projects.py:219`);
  `TRACK_KINDS["picture"]` *is* `source_filename` and `original-audio` is the hard-coded
  `audio.wav` (`api/routers/projects.py:392-398`); every output name is the source stem
  (`utils/helpers.py:163-169`, `services/revoice.py:118`). So the edit is **ranges of the
  one source** — an ordered list of kept `[start, end]` pairs. That single list expresses
  a split, a trim and a ripple delete, and it leaves the filmstrip, the waveform and every
  `/tracks/*` route exactly as valid as they are today.
- **Cutting the picture forfeits stream copy, and that is fine because a re-encode is
  cheap.** The re-voice's whole speed comes from `-c:v copy` (`core/video_creator.py:289`;
  stated at `services/revoice.py:8`), and there is **no code path anywhere that re-encodes
  a source video's picture** — `_build_video` only ever encodes images and moviepy clips.
  A stream-copy cut at an arbitrary point does not work: cut at 47.300 s, the output begins
  on **P-frames** (`0.000,P 0.033,P …`) with no reference picture, and the source's
  keyframe interval is exactly **2.000 s** (GOP 60 at 30 fps), so snapping to a keyframe
  can land a cut up to two seconds off — longer than most sentences. Measured instead: a
  full picture re-encode of the 341 s source at `libx264 -preset ultrafast` (the generate
  path's own settings, `core/video_creator.py:1163-1173`) takes **15.8 s**; the real EDL
  render — `trim`/`atrim` + `concat`, picture re-encoded, audio to AAC — also **15.8 s**;
  on the customer's bundled **ffmpeg 7.1 essentials** binary, **16.1 s**, frame-accurate
  (duration 336.008005 = 341.008 − 5.000, opening on an I-frame, largest gap across the
  join one frame). About 2.8 s per minute of 1080p30. `h264_nvenc` was *slower* (24.4 s).
  Re-encode everything, every render, as a job.
- **The narration lane already exists; it must be fed, not rewritten.** `assemble_master`
  (`services/processing.py:251-275`) lays sentences by pin with silence between, a pin is
  a floor, an overrun eats the next gap — and the client's `schedule()` mirrors it exactly
  (`frontend/src/lib/timeline.ts:311-338`), pinned by an anti-drift test that runs a real
  `_revoice_video` beside a real plan (`tests/test_narration_plan.py:362`). Projecting the
  transcript *before* handing it to that machinery keeps every one of the 13 sync tests
  green and extends the anti-drift test rather than breaking it. Nothing in
  `_revoice_video` changes.

What this is **not**: not multi-source, not transitions or effects, not a general NLE.
Section 9 lists what is deliberately left out and why.

---

## 1. Survey — what an edit collides with

### 1.1 The project record

`import_upload` writes nine keys (`services/projects.py:176-187`): `id, name, kind,
source_filename, size_bytes, slide_count, created_at, owner_id, owner_name`. Every later
key is written by a job or an editor, and the full inventory is:

| Key | Writer | file:line |
|---|---|---|
| `transcript`, `language`, `duration`, `transcribed_model`, `transcribed_device` | transcribe job | `services/transcription.py:60-64` |
| `transcript` (words only, merged) | whole-list Save | `services/projects.py:367` |
| `transcript[i]` + `offset/muted/voice/provider/speed` | narration editor | `services/narration.py:328`; vocabulary at `:67` |
| `rendered_at`, `output_video`, `outputs` | generate job | `api/routers/projects.py:264-277` |
| `revoiced_video`, `revoiced_at`, `narration_audio`, `revoiced_language`, `revoice_failed_sentences` | re-voice job | `services/revoice.py:143-169` |
| `images_source`, `images_rendered_at`, `slide_count`, `engine_project_dir` | slide editor | `services/slides.py:177-178, 488-489` |
| `qa_review`, `outputs["qa_doc"]` | AI jobs | `services/ai_slides.py:743, 947-949` |

`GET /{pid}` returns the **whole** record including the full transcript
(`api/routers/projects.py:114-116`), and `save_project` rewrites the whole file every time
(`services/projects.py:293-297`). The record is one JSON blob; anything added to it is
served and rewritten with everything else. The edit must therefore be **small** — a list
of ranges, never anything per frame — and written on a committed change, never per
pointer movement.

The **inner** engine record (`ProjectState`, `core/project_manager.py:48-66`) is rebuilt
from scratch on every re-voice (`services/revoice.py:91-116`). Nothing may be stored
there. The edit lives on the outer record only.

### 1.2 Every writer, and the lock

`project_lock(pid)` (`services/projects.py:71-82`) guards read-modify-writes of the outer
record; its contract is quoted at `:73-77` and the reason it lives in that module and not
in a feature module at `:24-29`. Writers that take it: `set_transcript` (`:349`),
`narration.update_segment` (`services/narration.py:318`), `delete_project` (`:226`). The
three job writers deliberately do not — they re-read immediately before saving and rely on
`require_idle` (`services/transcription.py:52-58`, `services/revoice.py:136-141`,
`api/routers/projects.py:258-260`).

Two writers already take the **wrong** lock and two take none: `services/ai_slides.py:739-762,
945-950` write the outer record under `slides.project_lock` (the inner file's lock, which
`services/projects.py:28` explicitly names as different), and `services/slides.py:169-181,
478-490` write it under no outer lock at all. They are masked today because they run inside
jobs. **This spec does not fix them** — it is a pre-existing smell, recorded here so the new
writer is not mistaken for their cause — but the new writer is *not* masked, because a
committed edit is a route, not a job, and it takes `store.project_lock` without exception.

A new writer of the record must (per `services/projects.py`'s own rules and the guards
that enforce them): re-read and save inside `store.project_lock(pid)`; call
`jobs.require_idle(pid)` from the route (`api/routers/narration.py:63` is the precedent);
register the route in `PROJECT_SCOPED_ROUTES` with a body that validates
(`tests/test_project_ownership.py:32-68`); and audit it with a **new named constant** in
`api/audit.py` (`tests/test_audit.py:39-55` parses the source and rejects inline strings).

### 1.3 The render, and the ffmpeg the codebase actually has

`_revoice_video` (`services/processing.py:1061-1354`) is the only render a video project
gets: per-sentence synthesis, `assemble_master` into one pydub track, then
`replace_video_audio` (`core/video_creator.py:300-372`), whose command is
`_build_replace_audio_cmd` (`:273-297`): `-c:v copy`, the new audio mapped in,
`apad=whole_dur=<d>` and `-shortest`. Its `<d>` comes from `_probe_duration` (`:260-270`),
a bare `"ffprobe"` that returns `None` on any failure.

The whole repository contains **no trim, no concat and no `filter_complex`** (grep over
`core/`, `services/`, `utils/`). The only `-ss` is a single-frame grab
(`core/video_importer.py:542-545`); the only `-t` caps a GIF at 30 s
(`services/processing.py:1658`). Concatenation exists only as pydub `+` (audio) and
moviepy `concatenate_videoclips(method="compose")` (`core/video_creator.py:1115`). Mixing
is one music file looped at a flat volume (`:327-342` — the only music support in the
codebase, and where E4 starts, §7); `core/audio_mixer.py:33`
(`AudioMixer`) looks like a mixer and is **dead code** — never instantiated. Likewise
`extract_keyframes`, `get_video_chapters` and `get_video_duration`
(`core/video_importer.py:384-604`) sit on no API path. None of these count as capability.

Eleven call sites spawn `"ffmpeg"` or `"ffprobe"` **by bare name** (`services/processing.py:1149`;
`core/video_creator.py:264, 286`; `core/video_importer.py:390, 543, 554, 571, 584, 600`;
`core/pptx_exporter.py:410`) and work only because `utils/config.py:70-78` prepends the
resolved binary's directory to `PATH` at import. New code uses `FFMPEG_PATH`
(`utils/config.py:67`) and never the bare name — the narration spec already forbids the
pattern (`docs/porting/narration-timeline.md:722-726`).

### 1.4 ffprobe does not exist where it matters

`_get_ffmpeg_paths` (`utils/config.py:31-63`) prefers system PATH, then `static-ffmpeg`,
then `imageio-ffmpeg` — which ships ffmpeg **only**. The packaged app stages exactly one
binary, `app/bin/ffmpeg.exe` (`desktop/boot.py:93-103`; `tests/test_no_state_tracked.py:42`),
and `_FFPROBE_PATH` is private to `utils/config.py`, imported nowhere — **no module can even
ask whether a probe is possible.** On the development machine both resolve to a WinGet
build (system PATH wins), which is why every measurement here was repeated on the bundled
7.1 binary before being believed.

Two subsystems were built to avoid ffprobe and tests lock it in: the waveform takes its
scale from the WAV header (`services/waveform.py:7-12, 221-250`;
`tests/test_waveform.py:435`), and the plan refuses it in a long comment
(`services/narration.py:550-571`). **The edit inherits the rule absolutely.** It never needs
a probe: the source's length is the WAV header's; the output's length is the sum of the
kept ranges, known before ffmpeg runs; and the mux's `whole_dur` is that sum, passed in
rather than probed.

### 1.5 The job model

`services/jobs.py`: a two-worker pool shared with GPU transcription and the AI loops
(`:34`), in-memory state (`:35`, docstring `:6-7`), `jobs.start(kind, work, project_id,
user_id)` (`:183-197`), `progress(fraction, message)` (`:88-95`), and one job per project
enforced by `require_idle` (`:176-180`, 409 at `api/app.py:71-74`). Cancellation is
cooperative (`:134-163`) — and `_revoice_video` **never consults it**
(`services/processing.py:1061-1354`); its subprocesses have fixed timeouts (600 s for the
mux, `core/video_creator.py:353`). A render is one more job kind and fits the model
unchanged, with two consequences accepted for v1 and one requirement: while it runs every
edit is a 409 (the render takes ~16 s, so this is tolerable; it is not what Camtasia does,
and section 9 says so); it holds one of two workers; and the **new** picture step must
poll `cancel_requested_here()` while ffmpeg runs (and stop it), and use a timeout scaled to
the **decode reach** — from the first kept start to the last kept end, never the output
length; E1's second and eighth notes in §7 — because the existing step does neither.

### 1.6 The frontend

`NarrationTimeline.tsx` already draws three lanes on one `pps` scale — filmstrip from a
hidden `<video>` seeked to `frameTimes(duration, n)` (`:505-568`), the waveform as one SVG
path (`:694-702`; rationale `frontend/src/lib/timeline.ts:151-160`), sentence blocks with
ghosts and overrun markers (`:704-759`) — with a ref-painted playhead (`:280-287`), zoom
(`:43`), click-to-seek, and a Web Audio transport whose `schedule()` mirrors
`assemble_master`. Its pure helpers are reusable as they are:

```
pixelsPerSecond(duration, width, zoom)      poolPeaks(peaks, width)      waveformPath(pooled, h)
frameTimes(duration, count)                 thumbCount(width)            schedule(sentences, clipSeconds, squeeze)
clipsFrom(clips, position)                  auditionLength(duration, end)   clampTime(t, duration)
```
(`frontend/src/lib/timeline.ts:90-352`.) Two limits matter: `schedule()` is
one-dimensional and audio-only, skipping any sentence with no decoded buffer
(`:319-320`) and never re-sorting (`:301-304`); and the filmstrip and transport drive **one**
`<video src=/tracks/picture>` (`:532, :767-774`). Both are fine for ranges of one source,
and both are why multi-source is out of scope.

There is **no undo stack** anywhere in the frontend. The only undo in the product is the
engine's 20-deep `notes_history` for speaker notes (`core/project_manager.py:284-288`).

---

## 2. Data model

One new key on the outer record:

```json
"edit": { "version": 1, "keep": [[0.0, 47.3], [52.3, 341.008]] }
```

- `keep` is an ordered list of `[start, end]` in **source seconds**, ascending,
  non-overlapping, `0 <= start < end <= source_duration`, where `source_duration` is the
  WAV header's (`services/waveform.py::duration_for`). At most a few hundred entries in
  any plausible edit; the record stays small (E1 bounds the list at `MAX_RANGES` = 5000 —
  a bound on abuse, not on use).
- **Absent means keep everything.** Every existing project renders byte-identically to
  today, and the first cut a user makes creates the key. No migration.
- `version` is there so a later shape can change without guessing what an old record
  meant: E4's music lane is version 2 (§7); multi-source and mixing the original audio come
  after that. `stored_keep` refuses any version but its own, so whoever bumps it must read
  the older shape as well, never refuse it.
- The edit is the **only** thing stored. Split, trim and ripple delete are all changes to
  `keep`: a split adds a boundary (two ranges where there was one, contiguous); a trim
  moves a range's edge; a ripple delete removes a sub-range and the ranges either side
  stay where they are in source time. There is no clip object, no track object, no
  position field — position is *derived* (§3), which is what keeps the transcript's
  coordinates untouched.

Validation lives with the rest of the record's rules, in a new pure module
`services/edit.py`, and the schema for the route is a pydantic model with
`model_config = ConfigDict(extra="forbid")` — the same lesson `TranscriptSegment` learned
in phase 1, where the default `extra="ignore"` would have silently discarded fields.

The transcript is **not** touched by any of this. Its `start`/`end` remain source seconds.
That is the entire point.

---

## 3. Projection — the one piece of arithmetic

`services/edit.py` owns four pure functions, unit-tested to exhaustion, and both the plan
and the render call them — never a copy (E1 put them behind one entry point, `apply`, which
is the single call both `services/revoice.py` and `narration.plan` make; the module also
owns `validate_keep`, `whole_source` and the two store functions — §7):

```
output_duration(keep) -> float                 # sum of (end - start)
to_timeline(t_source, keep) -> float | None    # None when t falls in a removed range
to_source(t_timeline, keep) -> float           # the inverse, for the playhead and the filmstrip
project_transcript(transcript, keep) -> list   # the sentences the render will speak, in timeline seconds
```

`project_transcript` is the rule that decides what happens to a sentence at a cut, and it
is deliberately simple so it can be explained in one sentence to the person cutting:

- A sentence is **kept iff its `start` lies inside a kept range** (`[start, end)`, so a
  sentence beginning exactly where a cut begins goes with it). Its `start` becomes
  `to_timeline(start)` and its `end` becomes `min(end, range_end)` mapped likewise; its
  `offset` rides through **untouched**, so the pin the engine computes (`_pin`) is
  `to_timeline(start) + offset`, the offset applied once. *Corrected by E1:* this used to
  read `to_timeline(start + offset)`, which has no answer when an offset crosses a cut and
  would have applied the offset twice.
- A sentence whose `start` lies in a removed range is **left out**, exactly as a muted one
  is today (`services/processing.py:1167-1170` filters `spoken` before any window maths,
  so the room it occupied goes to the sentence before it — the existing rule, unchanged).
- Every override key rides through untouched, because the sentence object is the same
  dict with two numbers remapped. `_same_sentence` on disk is never consulted, because
  nothing on disk changed.

That output list is what `services/revoice.py` hands to the engine as
`slide0.original_segments` (`services/revoice.py:100-106`), and it is what `narration.plan`
reports. *As built:* the section is `transcript_section(projected sentences)` — its end is
the last kept sentence's clamped end, not `output_duration(keep)` as this first read — and
it is the plan's `duration` that is the output's length (E1's ninth note, §7). `_revoice_video` sees a transcript in a shorter
recording and does what it always does. The anti-drift test's job — "the plan's speeds are
the speeds the render really synthesises at" — gains a `keep` parameter and keeps holding.

The client gets the same four functions in `frontend/src/lib/edit.ts` (E2 — not built),
tested against the same fixtures. This is the one deliberate duplication, and it is the same kind phase 3a
accepted for `schedule()`: the client needs `to_source`/`to_timeline` to draw and to seek,
and a round trip per pointer movement is not an option. The tests share fixtures so the
two cannot drift silently.

---

## 4. The render — precisely what changes

**Nothing in `_revoice_video`.** The picture step is inserted *before* the mux and only
when `keep` does not cover the whole source; with no edit the path is today's, byte for
byte, and `tests/test_revoice_sync.py` never notices.

1. `services/revoice.py` reads `record.get("edit")`, validates it, computes
   `project_transcript` and `output_duration`, and builds the engine state from the
   projection (§3). Unchanged otherwise. (Shipped as one call, `edit.apply`, whose result
   also says whether the picture needs cutting at all.)
2. **New:** `core/video_creator.py::cut_picture(source, keep, dst, video_bitrate,
   cancel_check)` (as shipped — the plan's `fps` parameter never existed, since no rate is
   passed) — one ffmpeg invocation, `FFMPEG_PATH`, never the bare name:

   ```
   -ss S0 -i src                                   # an INPUT seek to the first kept start (E1)
   -filter_complex "[0:v]trim=start=S0−S0:end=E0−S0,setpts=PTS-STARTPTS[v0];
                    [0:v]trim=start=S1−S0:end=E1−S0,setpts=PTS-STARTPTS[v1]; …
                    [v0][v1]…concat=n=N:v=1:a=0[v]"   # every trim offset by S0: the seek resets the timestamps
   -map "[v]" -an -c:v libx264 -preset ultrafast [-b:v <preset bitrate>] -y dst
   ```
   Picture only, no audio (`-an`): the narration is muxed on afterwards by the step that
   already exists. **No `-r` at all.** `trim` + `setpts=PTS-STARTPTS` + `concat` carry the
   source's own cadence through — the proof render above passed no rate and came out at
   exactly 30 fps (10079 frames over 335.967 s) — and there is no ffprobe to learn a rate
   from anyway. What matters is that it is never `fps_for_transition`
   (`core/video_creator.py:35-41`), which chooses very low rates for static decks and would
   wreck a screen recording; not forcing a rate satisfies that by construction. The bitrate
   comes from the output preset (`services/output_presets.py`), `""` meaning codec default,
   exactly as `write_videofile` takes it (`core/video_creator.py:1169`); in E1 that is the
   default preset (`youtube_1080p`), and choosing one is E2's Render button. *The next two
   rules were wrong as first written and are corrected here (E1's second and eighth notes,
   §7):* the step polls `cancel_requested_here()` every half second **while ffmpeg runs**
   and kills it on a cancel — there is no "between ranges", the cut is one run — and its
   timeout is `cut_timeout(keep) = 60 + 3 × (keep[-1][1] − keep[0][0])` seconds: the
   **decode reach**, never the output's length (measured rate ≈ 2.8 s per minute of footage
   decoded, with headroom). Writes to a `.part` and publishes with `replace_with_retry`.
3. `replace_video_audio(cut_picture_output, master, out, …)` as today. The one change this
   step asked for — the video duration as an **argument**, supplied by the caller as
   `output_duration(keep)`, with `_probe_duration` only when the caller passes `None`, so
   the edited mux never needs ffprobe — shipped as `video_duration` on
   `replace_video_audio` (`_build_replace_audio_cmd` already took one), tested, and
   **unwired**: "the caller" is `_revoice_video`, and this section's first sentence says
   nothing in it changes. The first sentence wins. An edited run still probes, or pads to
   infinity where there is no ffprobe, which is unobservable under `-shortest` (E1's third
   note, §7).
4. `record["edit_rendered_at"]` is stamped beside `revoiced_at`, for the same reason
   `revoiced_at` exists (`services/revoice.py:143-151`): the output filename does not
   change, so the page's cache-buster must. Shipped; a whole-source run clears it, so the
   record never claims an edit the file on disk does not carry.

Measured cost on the 341 s source: 15.8 s for the picture, ~3 s for the mux, plus
narration synthesis that is cached after the first audition. Under 25 s end to end. A
20-minute recording is about a minute.

The original audio is **not** part of the v1 output — the narration replaces it, exactly as
a re-voice does today. It is drawn on the timeline as reference only. Keeping it or ducking
it under the narration is v2 (§9); music over the top is **E4** (§7); and `AudioMixer` will
not be the starting point for either, because nothing has ever run it.

---

## 5. API

New router `api/routers/edit.py`, `prefix="/projects"`, following `narration.py`:

| Route | Notes |
|---|---|
| `GET /{pid}/edit` | `{"version": 1, "keep": [[…]] or null, "source_duration": …, "output_duration": …}` — `null` meaning everything, the source's length from the WAV header (`null` until the video is transcribed), the output's the sum of the ranges. `readable` only; allowed during a job — it is a read. 400 for an edit this version cannot read. |
| `PUT /{pid}/edit` | Replaces the whole list (it is small; a partial PATCH buys nothing). `require_project` → `jobs.require_idle` → validate → `store.project_lock` → re-read → save (validated before the lock, so a refused list leaves the record untouched). Audits the new constant `PROJECT_EDIT = "project.edit"` with detail = the range count and the seconds removed (`"2 ranges kept, 5.0 s removed"`), never the times. `extra="forbid"` and strict numbers (422). 400 naming the range for overlap, order, a bound past the source, NaN or infinity; 409 while a job holds the project **or while the video has no extracted audio** — the ranges are checked against its length. Answers the GET's shape. |
| `DELETE /{pid}/edit` | Back to keep-everything: the key is removed, not written empty. Same guards. Idempotent — a project with no edit is left as it is and nothing is audited. |
| `POST /{pid}/revoice` | The render. It **is** the re-voice job with the projection applied — one job kind, one code path — and E1 shipped exactly that: there is no `/render` route, the existing re-voice route starts it and the job reads the edit it renders. Listed here so the choice is explicit: **no new job kind.** |

`GET /{pid}/narration/plan` and `GET /{pid}/waveform` gain nothing in their URLs; the plan
applies the projection server-side and returns sentences already in timeline seconds — each
still carrying its `index` in the *stored* transcript, which is what the narration routes
address a sentence by — with `duration` the output's length, plus
`edit: {version, keep, source_duration, output_duration}` (the GET's shape) so the client
draws the same edit the plan was made from.
The waveform stays in source seconds — it is one WAV file — and the client projects it
(§6). Three routes join `PROJECT_SCOPED_ROUTES`; one joins the audit guard.

---

## 6. The UI — extend the timeline, add one gesture

Phase 3a's `NarrationTimeline` is extended, not replaced. `TranscriptCard` keeps its
List / Timeline switch. What changes:

- **The strip is drawn in timeline seconds**, holes closed — the Camtasia feel, and the
  thing the owner asked for. The waveform is `poolPeaks` over the peaks array **sliced and
  concatenated by `keep`** (a bucket is 125 ms of one file, so keeping ranges is an array
  operation); the filmstrip seeks the hidden `<video>` to `to_source(frameTime)`; blocks
  come from the plan already projected. `pictureWidth` becomes `output_duration × pps`.
- **Range selection**: drag on the ruler, or set in/out at the playhead with `I`/`O`, then
  **Ripple delete** (`Delete`). Both are one `PUT /edit` with the new list, committed on
  release — never per frame (`docs/porting/narration-timeline.md:715-717`).
- A removed range leaves a **thin marker** at the join, so a cut is visible after it has
  closed up, with the removed length in its tooltip — Camtasia shows nothing there, and
  nothing is how mistakes go unnoticed.
- **Undo/redo** is a client-side stack of `keep` snapshots (50 deep), because there is no
  server-side undo anywhere and the list is small enough to snapshot whole. `Ctrl+Z`
  re-`PUT`s the previous list. Lost on reload; the server holds only the current state.
  Honest about that in the UI.
- The transport plays the **projected** schedule — `schedule()` needs no change, it is
  given projected sentences — and the muted `<video>` is seeked to `to_source(position)`
  at every jump. Between ranges the picture skips; that is the edit.
- **Render** replaces "Re-voice again" on the card when an edit exists, and says what it
  will do: "Cut 2 ranges (12.4 s) and re-voice — about 20 s."
- **A continuous zoom slider**, as Camtasia has, in place of the stepped buttons. Zoom
  exists today as four fixed steps — `ZOOMS = [1, 2, 4, 8]` (`NarrationTimeline.tsx:43`)
  behind + and − (`:652-666`) — and on a first open it appeared to do nothing, because the
  strip was drawing at zero scale (fixed at `4f39ae1`). The arithmetic already takes any
  factor: `pixelsPerSecond(duration, width, zoom)` (`frontend/src/lib/timeline.ts:102`)
  is continuous, and only the step array makes it discrete. The slider runs from **fit**
  (the whole edit in the strip's width, zoom 1) to a ceiling where one second is about
  200 px, on a logarithmic scale so the low end is not wasted; a **Fit** button beside it
  returns to 1. Zooming keeps the **playhead** at the same screen position — the strip's
  `scrollLeft` is recomputed from `to_px(position)` on every change, which is what makes
  zooming in to trim a cut and back out to see the whole feel like one motion rather than
  a hunt. `Ctrl`+wheel over the strip zooms about the pointer instead. The filmstrip
  regenerates only past `needsNewFrames`'s 120 px tolerance (`timeline.ts:207`), as now,
  so dragging the slider does not seek the video continuously. The peaks are pooled per
  zoom from the same cached array; nothing is fetched.

What stays typed rather than dragged: nothing new. Per-sentence offsets remain in the
List view as phase 3a left them; the edit gesture is a separate concern on a separate
lane.

---

## 7. Build order

**E1 — the model and the render, no UI.** **DONE**, 2026-09-16; see CHANGELOG
`#edit-timeline`. `record["edit"] = {"version": 1, "keep": [[start, end], …]}` in source
seconds, absent meaning keep everything; `services/edit.py` (`validate_keep`,
`output_duration`, `to_timeline` / `to_source`, `whole_source`, `project_transcript`,
`stored_keep`, `apply` — the one entry point both callers use —, `payload`, `describe`,
`set_edit`, `clear_edit`; `MAX_RANGES`, `PRECISION`, `EPSILON`); `EditIn` in
`api/schemas.py`; `GET` / `PUT` / `DELETE /{pid}/edit` in the new `api/routers/edit.py`
over `PROJECT_EDIT` in `api/audit.py`, the three routes in the ownership sweep; the render
as the re-voice job in `services/revoice.py` — `edit.apply`, `cut_picture` into the job's
scratch directory, the engine pointed at the cut, `edit_rendered_at` stamped and cleared;
`cut_picture` / `cut_filtergraph` / `cut_timeout` / `CUT_POLL_SECONDS` and the
`video_duration` argument on `replace_video_audio` in `core/video_creator.py` (this
phase's plan put the argument on `_build_replace_audio_cmd`, which already had one);
`narration.plan` returning the projected sentences (each with its `index` in the stored
transcript), the output's `duration` and the `edit` block; `readable_project` /
`writable_project` in `api/deps.py`; `NO_AUDIO_MESSAGE` in `services/waveform.py`. Proven
without a browser exactly as this paragraph asked — `PUT` a `keep`, `GET` the plan, render:
the 5 s cut on the corpus source came out 336.008 s by the WAV arithmetic and 10079 frames /
335.967 s by the picture (its video stream is 41 ms shorter than its audio), first frame an
I-frame at 0.000, framemd5-identical to the un-seeked graph. `frontend/` is untouched.
Tests: `tests/test_edit.py` (80 — 41 functions, three parametrised: the arithmetic to
exhaustion, the routes, the plan), `tests/test_generation_options.py` (+10, the picture
step against a fake ffmpeg it polls, and the mux told its length), `tests/test_revoice.py`
(+9, the render as the re-voice job), `tests/test_narration_plan.py` (+1, the anti-drift
pin's `keep` case — a real plan beside the real `revoice_project` under the same edit),
`tests/test_project_ownership.py` (+3 routes in the sweep) — **703 backend and 103 vitest
across the suites**, ruff clean. Deliberately left: **the UI** — no gesture, nothing on the
strip is drawn in timeline seconds, and a project carrying an edit shows the phase-3a
timeline with *projected* blocks over a filmstrip and waveform still in source seconds
(E2); the `video_duration` argument, which nothing passes (the third note below); the
client copy of the projection (`frontend/src/lib/edit.ts`, E2); and per-range input seeking
(the second note below).

Nine things the build changed about the design above. Read them before E2, because several
contradicted a line that read as written until E1 corrected it in place:

- **§3's pin formula was wrong and the build is right.** The projection remaps `start` and
  `end` only and leaves `offset` exactly as stored; the engine's `_pin`
  (`services/processing.py`) adds the offset once, so the pin is `to_timeline(start) +
  offset`. The formula as first written, `to_timeline(start + offset)`, has no answer when
  an offset crosses a cut — the moment it names is in the hole — and applying the offset in
  the projection as well would have applied it twice. §3 is corrected in place, because it
  is the formula the next reader (E2's `edit.ts`) will copy.
- **§4's timeout rule `60 + 3 × output_duration` was wrong, and the Reviewer proved it on
  real footage.** `trim` is a filter and runs *after* the decode, so the work is bound by
  how far into the source the last kept range **ends**, not by how much comes out: a 5 s
  keep at the tail of the 341 s corpus source cost 3.9 s, and a seekless cut on a two-hour
  source was killed by its own 75 s timeout (`cut_picture`'s docstring records both).
  Shipped: an **input seek**, `-ss <first kept start>` before `-i` — which resets the
  input's timestamps, so every trim in the graph is offset by that origin — and
  `cut_timeout(keep) = 60 + 3 × (keep[-1][1] − keep[0][0])`. The seek is frame-exact: the
  decoded frames' `-f framemd5` output is identical with and without it (a dev-only check,
  since the suite fakes ffmpeg; the recipe is in the docstring), and the tail keep fell to
  0.87 s. **The reusable rule:** size any filtergraph cut by its decode reach — the first
  kept start to the last kept end — and move the start of that reach with an input seek.
  The remaining limit: a head-plus-tail keep still decodes the whole file (correctly timed
  now, rather than wrongly killed), and the fix when it matters is one seeked input per
  range, concatenated — per-range input seeking.
- **§4 step 3 contradicted §4's own first sentence, and the first sentence wins.** "The
  caller supplies the duration" — but the caller of the mux *is* `_revoice_video`, and the
  section opens with "nothing in `_revoice_video`". The argument shipped (`video_duration`
  on `replace_video_audio`; `_build_replace_audio_cmd` already took one), is tested, and is
  **unwired**: an edited run still probes, or pads to infinity where there is no ffprobe.
  Measured unobservable: the mux is `-shortest`, and a plain `apad` and
  `apad=whole_dur=336.008` produce identical output on the edited corpus render. Recorded in
  the function's docstring and here rather than hidden; it is wired the day
  `_revoice_video` is opened for some other reason.
- **Trap 11 is false with offsets.** A sentence's *start* is clamped inside the output by
  the projection, but its pin is that start plus an offset the projection does not touch,
  so a sentence can still be pinned at or past the output's end under an edit and
  `past_end` fires exactly as it does unedited — the plan marks it, the audition skips it,
  the render's `-shortest` drops it. That machinery stays load-bearing; trap 11 is corrected
  in place.
- **Trap 14's "an edit can land mid-job" is false.** `PUT` and `DELETE /edit` take
  `jobs.require_idle` like every other write of the record, so nothing lands on a project
  while a job holds it. The re-read in the generate job still matters, for the reason the
  comment in `api/routers/projects.py` (fixed in E1, as trap 14 asked) now gives:
  `record_images_source` writes the record earlier in the *same* job, and a write that
  passed `require_idle` an instant before the job was registered can land on it too; the
  captured copy would revert either. §1.2's picture — job writers re-read and rely on
  `require_idle` — was right; the parenthetical was not.
- **`to_source` at a join returns the later range.** The end of one kept range and the
  start of the next are the same output instant, and `to_timeline` maps both to it; but the
  earlier range's end is a frame the output never shows (`trim`'s end is exclusive), so a
  filmstrip or a playhead seeked there would show a cut frame. The later range wins, the
  round trip still holds, and E2's `edit.ts` must copy that rule rather than take the first
  match.
- **Three consolidations the router forced.** The narration, slides and edit routers'
  `readable` / `writable` (`guard` / `writable`) pairs were one thing spelled three times;
  they live once in `api/deps.py` as `readable_project` / `writable_project`, with the
  order (403 before the kind, the kind before the job) pinned by the ownership sweep.
  `clear_edit` returns `(payload, had_edit)`, so a DELETE on a project with no edit writes
  nothing and is not audited — an audit row for a no-op would be a lie. And the
  "transcribe first" sentence the tracks route, the waveform's 404 and the edit's 409 all
  say is one constant, `waveform.NO_AUDIO_MESSAGE`, shared with `TRACK_KINDS`.
- **Cancel during the cut stops ffmpeg**, which is more than `_revoice_video` has ever
  done. §1.5 asked the step to check the flag "between ranges", but the cut is one ffmpeg
  run over every range, so there is no between: `cut_picture` runs it under `Popen`, polls
  the flag and its deadline every `CUT_POLL_SECONDS` (0.5 s), kills the process on either,
  and leaves nothing behind (the `.part` and the log are removed). The job ends as
  cancelled with nothing rendered and nothing stamped. The flag is read once more before
  the publish, so a cancel that lands between ffmpeg finishing and the rename discards the
  finished part.
- **The section end is the last kept sentence's, not `output_duration(keep)`.** §3 said
  the engine's section end would be set to the sum of the ranges; as built the section is
  `transcript_section(projected sentences)` — the one helper the plan and the render
  share — so its end is the last kept sentence's clamped end, and it is the plan's
  `duration` that is the output's length. The consequence is the one phase 3a already
  accepted: the last spoken sentence's window is bounded in the render by ffprobe of the
  cut picture (the sum less the 41 ms the video stream is shorter by), or by the section's
  end where there is no ffprobe, and in the plan by the WAV-derived sum. Blast radius
  unchanged: the last sentence, only when the bound would make it short enough to speed up.

Three refusals E1 added that the design did not specify, and E2 must show rather than
swallow: an edit written by a different `version` is a 400 on the GET and on the plan and
an error from the render, never a silent keep-everything (rendering the whole video while
the user believes a cut is applied is the one quiet failure this must never have); an edit
whose `audio.wav` has since gone is read back with `source_duration: null` but neither
applied nor ignored — the plan is a 400 and the render fails saying so; and an edit that
removes every spoken sentence fails the render with "keep at least one, or clear the edit".
Storing or clearing an edit also drops the memoised speaking rate (`forget_baseline`),
because the rate is measured over the sentences the render will speak and the edit decides
which those are.

**E2 — the gesture.** Timeline-second drawing, range selection, ripple delete, the join
marker, the undo stack, the Render button. This is the phase the owner drives.

**E3 — the rest of Camtasia's editing set, in this order:** trim handles on a kept range's
edges; split at the playhead as a first-class gesture (today it is "select a zero-length
range"); markers; keyboard `J`/`K`/`L`; snapping to the playhead and to sentence pins.

**E4 — the music lane.** Asked for by the owner on 2026-09-16 — *"I should be able to add
music tracks over the top — another channel like Camtasia"* — and moved here out of §9's
v2 list (§9 keeps mixing and ducking the *original* audio). Sketched to the model rather
than designed: a second lane on the same timeline, which must fit the one-list shape above.

- **The model.** `edit.version` 2 gains `"music": [{file, at, in, out, gain, fade_in,
  fade_out}]` — `file` a name in the music **library**, `at` where the clip starts in
  **timeline seconds** (the output's: music is placed on the cut, not on the source, so a
  ripple delete before it moves it with the picture, which is what a Camtasia lane does),
  `in` / `out` the slice of the file used, `gain` the clip's level, `fade_in` / `fade_out`
  in seconds. `keep` is unchanged. The bump means `stored_keep`, which refuses any version
  but its own, must read a version-1 record as "no music" rather than refuse it; and the
  route payload and the plan's `edit` block carry the lane, so E2's drawing has it from the
  same fetch as the ranges.
- **The library.** Upload / list / delete under `assets/music` — porting vertical 2b
  (`docs/porting/generation-options.md` §4), surveyed 2026-09-14 and never built: today
  `assets/music/` does not exist and nothing serves it. The library API (filenames
  validated like `_PID_RE`, audited) is the prerequisite, and it is the same work 2b needs
  for the generate path, so it is built once, for both.
- **The render** generalises the one-file pydub overlay in `replace_video_audio`
  (`core/video_creator.py:327-342` at `4f39ae1`; the function gained the `video_duration`
  lines above it in E1) — one file, looped to the narration's length, at a flat volume
  from Studio settings; the only music support there is — to clips placed at `at`, sliced
  `in`–`out`, faded, and summed under the master before the mux. `AudioMixer` is still not
  the starting point (§1.3).
- **The audition** schedules a music `AudioBuffer` at `at` exactly as `schedule()` places
  the sentence clips, on the same Web Audio clock, with the clip's gain and fades applied
  there; the library file is fetched and decoded once, like a sentence clip.
- **Two rules the owner set earlier bind it, and neither is a design question:** **no
  ducking** — the music sits at one static lower volume under the voice for the whole clip
  (dynamic ducking was rejected for the choppy playback it produced) — and **music may
  fade in and out while the voice never does** (the narration starts and stops clean, as
  `assemble_master` leaves it).

Sequenced **after E2**, because the lane needs E2's timeline-second drawing and transport
to be placed and heard at all; it does not depend on E3.

Budget E1 as phase-3a-sized and E2 as larger than E1: the drawing change touches every
lane and the transport, and it is where the first-open-at-zero-scale class of bug lives.
Every phase passes Developer → Reviewer → Documentation, and E2 is **rendered in the
packaged app before it is believed** — three gates missed 3a's zero-scale strip because
nobody looked.

---

## 8. Traps

1. **Never move the transcript.** If an implementation finds itself rewriting `start`/`end`
   on disk after a cut, it has re-created the dropped-adjustments bug (`services/projects.py:303-316`).
   Project; do not mutate.
2. **No ffprobe, anywhere, ever.** `_FFPROBE_PATH` cannot be queried from outside
   `utils/config.py`; the packaged app has none. Source length = WAV header; output length =
   `output_duration(keep)`; pass it into the mux. (E1: the argument to pass it exists on
   `replace_video_audio` and is unwired — §7, third note — so an edited mux still probes;
   harmless under `-shortest`.)
3. **`FFMPEG_PATH`, not `"ffmpeg"`.** The eleven bare-name sites work by accident of
   `PATH` mutation. The new step is the first code that runs *only* in an edited project
   and must not inherit the accident.
4. **Stream copy cannot cross a cut.** Measured: P-frames at the head, 2 s GOP. Re-encode.
   If someone later proposes "copy the middle ranges and re-encode only the boundary GOPs",
   the answer is the benchmark: 15.8 s for the whole file is not worth the complexity.
5. **No `-r` at all.** `trim` + `setpts` + `concat` carry the source's own cadence through
   (measured: 30 fps in, 30 fps out), and `cut_picture` passes no rate — its test forbids
   one. What this trap guards is that it is never `fps_for_transition`: a screen recording
   rendered at 2 fps is the bug that would follow. (This read "`-r` is the source frame
   rate" until E1, while §4 already said no `-r`; the build is the latter.)
6. **`extra="forbid"` on the edit schema.** Phase 1's lesson, and the whole-list PATCH still
   pays for it (`tests/test_narration_overrides.py:332-341`).
7. **Take `store.project_lock`; never `slides.project_lock`; never none.** The two wrong
   writers in `ai_slides.py` are a smell, not a precedent.
8. **Commit on release, never per frame.** The record is rewritten whole and served whole.
9. **The new picture step must check cancellation** and scale its timeout; the existing
   render checks nothing and has a fixed 600 s. Done in E1 — the flag polled while ffmpeg
   runs and the process killed; the timeout scaled to the **decode reach**, not the output
   (§7, second and eighth notes).
10. **Write to `.part`, publish with `replace_with_retry`.** Windows sharing refusals are
    measured at 2 in 60 (`utils/helpers.py:21-55`), and a render holds several files open
    across seconds.
11. **A sentence can still be pinned past the output's end — by its offset.** The
    projection drops sentences in removed ranges and clamps the rest's `start` to the
    output, but the offset is applied after the projection (§3), so an offset can carry a
    pin past the end under an edit exactly as it can without one. The `past_end` machinery
    (`services/processing.py:1181-1194`, `frontend/src/lib/timeline.ts:38-39`) stays
    load-bearing in both cases. (This read "impossible by construction" until E1; §7,
    fourth note.)
12. **The `revoice_sync_mode == "free"` branch is ignored.** Nothing writes it
    (`core/project_manager.py:66`; no route sets it); an edit is meaningless without pins.
13. **`edit_rendered_at` busts the cache** for the same reason `revoiced_at` does — same
    filename every run (`services/revoice.py:143-151`, `MUTABLE_MEDIA_HEADERS`). Shipped in
    E1, and cleared by a whole-source run.
14. **The stale comment at `api/routers/projects.py:258-260`** said a preview and a full
    render can run at once; `require_idle` refuses the second. Fixed in E1. The re-read is
    still right, but **not** because "an edit can land mid-job", as this trap first said:
    `PUT` / `DELETE /edit` take `require_idle`, so no edit lands on a project a job holds.
    It is right because `record_images_source` writes the record earlier in the same job,
    and a write that passed `require_idle` an instant before the job was registered can
    land on it too (§7, fifth note).
15. **`data/cache` grows** exactly as before (trap 19 of the narration spec). Unchanged, and
    still not to be swept.
16. **Dead code that looks like capability**: `AudioMixer`, `extract_keyframes`,
    `get_video_chapters`, `get_video_duration`. None is on a path; none is a starting point.
17. **Two different ffmpegs.** Development resolves a WinGet 8.0.1 full build; customers run
    the bundled 7.1 essentials. Every claim here was measured on both. A filter added later
    must be checked against 7.1's `-filters` before it is relied on.

---

## 9. What NOT to build

- **Multi-source clips.** Twelve places assume one source (§1.1); the filmstrip and the
  transport drive one `<video>`; the waveform is one WAV. Ranges of one source deliver
  cutting, trimming and ripple delete — which is what the owner cuts *for*. A second source
  is v2, with its own `version` of the edit and its own survey.
- **Keeping or mixing the original audio, ducking.** v2. The narration replacing the
  original is today's product and the reason people use it. Music over the top is **no
  longer on this list**: it is E4 in §7, at the owner's request (2026-09-16).
- **Transitions, callouts, cursor effects, animations, green screen, multi-camera,
  quizzing, screen recording.** Camtasia's, not ours. None serves a narrated demo.
- **Editing while a render runs.** Camtasia does; the one-job rule forbids it; the render
  is ~16 s. Accepted for v1, recorded as the thing to revisit if renders grow long.
- **A server-side undo.** The list is small; snapshots on the client are enough until they
  are not.
- **A stream-copy fast path.** See trap 4 and the benchmark.

---

## 10. Decisions — taken by the owner 2026-09-16 ("go with your proposals")

1. **A sentence that straddles a cut is kept iff its `start` is kept** (§3). Simpler to
   explain, and it matches how mute already works. The alternative — kept if more than half
   survives — was considered and not chosen; it is a one-line change in
   `project_transcript` if it is ever wanted, and it must stay one line.
2. **The join marker is in.** Small, with the removed length in its tooltip (E2).
3. **E1 is committed unversioned**, like phase 3a was. E2 is what gets a number.

The owner's standing instruction for E2 and E3 is **"copy Camtasia"** — the gestures, the
look and the feel of its timeline, within the scope §9 draws.

### Critical files

| File | Role |
|---|---|
| `services/edit.py` (new) | validation, `to_timeline`/`to_source`, `project_transcript`, `output_duration` |
| `services/revoice.py:79-118` | apply the projection before building the engine state |
| `services/narration.py::plan` | return projected sentences and the edit |
| `core/video_creator.py` | `cut_picture`, `cut_filtergraph`, `cut_timeout` (E1); `video_duration` on `replace_video_audio` (E1, unwired) |
| `api/routers/edit.py` (new) | the three routes |
| `api/audit.py` | `PROJECT_EDIT` |
| `api/deps.py`, `api/schemas.py` | `readable_project` / `writable_project`; `EditIn` (E1) |
| `frontend/src/lib/edit.ts` (new, E2) | the client copy of the projection, shared fixtures |
| `frontend/src/components/project/NarrationTimeline.tsx` (E2) | timeline-second drawing, selection, ripple delete, undo |
| `tests/test_edit.py` (new), `tests/test_revoice.py`, `tests/test_generation_options.py`, `tests/test_narration_plan.py`, `tests/test_project_ownership.py`, `tests/test_audit.py` | the guards |
